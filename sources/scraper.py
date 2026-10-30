"""A small, polite scraper for the FDA's public recalls listing (a US government work, public domain).

Polite means: it reads robots.txt first and stops if the path is disallowed, sends an honest User-Agent, waits
at least `delay` seconds between requests (or the site's Crawl-delay if larger), fetches at most `max_pages`
pages, and caches every page on disk so re-runs do not hit the site again.
"""

from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

FDA_RECALLS = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"
USER_AGENT = "local-llm-extractor/0.1 (portfolio project; https://github.com/MichaelH117)"


class RobotsDisallowed(RuntimeError):
    pass


@dataclass
class PoliteFetcher:
    cache_dir: Path
    delay: float = 3.0
    user_agent: str = USER_AGENT
    session: requests.Session | None = None
    _last: float = 0.0
    requests_made: int = 0

    def __post_init__(self):
        self.session = self.session or requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        root = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(self._get(urljoin(root, "/robots.txt")).splitlines())
            self._robots[root] = rp
        return self._robots[root]

    def _get(self, url: str) -> str:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(url, timeout=30)
        self._last = time.monotonic()
        self.requests_made += 1
        r.raise_for_status()
        return r.text

    def fetch(self, url: str) -> str:
        """Return the page HTML from the cache, or fetch it if robots.txt allows."""
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".html")
        if path.exists():
            return path.read_text(encoding="utf-8")
        rp = self._robots_for(url)
        if not rp.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(url)
        self.delay = max(self.delay, float(rp.crawl_delay(self.user_agent) or 0))
        html = self._get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return html


def parse_recalls(html: str, base: str = FDA_RECALLS) -> list[dict]:
    """Rows of the recalls table -> dicts. Column headers are read from the page, not hard-coded positions."""
    table = BeautifulSoup(html, "html.parser").find("table")
    if table is None:
        return []
    heads = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    want = {"Date": "recall_date", "Brand Name(s)": "brand", "Product Description": "product",
            "Product Type": "product_type", "Recall Reason Description": "reason", "Company Name": "company"}
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        pairs = zip(heads, cells, strict=False)
        rec = {want[h]: " ".join(c.get_text(" ", strip=True).split()) for h, c in pairs if h in want}
        link = tr.find("a")
        rec["url"] = urljoin(base, link["href"]) if link and link.get("href") else None
        rows.append(rec)
    return rows


def scrape_fda_recalls(cache_dir: Path, fetcher: PoliteFetcher | None = None) -> list[dict]:
    """One page: the 10 most recent recalls in the server-rendered table (later pages are loaded by JavaScript,
    and `?page=1` returns the same rows, so one polite request is all this needs). De-duplicated by URL."""
    f = fetcher or PoliteFetcher(cache_dir)
    seen, rows = set(), []
    for rec in parse_recalls(f.fetch(FDA_RECALLS)):
        if rec["url"] not in seen:
            seen.add(rec["url"])
            rows.append(rec)
    return rows
