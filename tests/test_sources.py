"""Scraper and API helpers, with a fake HTTP session (no network in unit tests)."""

import pytest

from sources.nhtsa import component_categories
from sources.scraper import FDA_RECALLS, PoliteFetcher, RobotsDisallowed, parse_recalls, scrape_fda_recalls

PAGE = """<html><body><table>
<tr><th>Date</th><th>Brand Name(s)</th><th>Product Description</th><th>Product Type</th>
<th>Recall Reason Description</th><th>Company Name</th><th>Terminated Recall</th></tr>
<tr><td>10/29/2026</td><td><a href="/safety/recalls/x-1">Acme</a></td><td>Cinnamon</td><td>Food</td>
<td>Potential   lead contamination</td><td>Acme Inc.</td><td></td></tr>
<tr><td>10/28/2026</td><td><a href="/safety/recalls/x-2">Beta</a></td><td>Salad kit</td><td>Food</td>
<td>Undeclared sesame</td><td>Beta Farms</td><td></td></tr>
</table></body></html>"""


class FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, robots="User-agent: *\nAllow: /\n"):
        self.headers, self.urls, self.robots = {}, [], robots

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResp(self.robots if url.endswith("/robots.txt") else PAGE)


def test_parse_recalls_reads_headers_and_links():
    rows = parse_recalls(PAGE)
    assert [r["brand"] for r in rows] == ["Acme", "Beta"]
    assert rows[0]["reason"] == "Potential lead contamination"
    assert rows[0]["url"] == "https://www.fda.gov/safety/recalls/x-1"


def test_robots_checked_first_then_cached(tmp_path):
    s = FakeSession()
    f = PoliteFetcher(tmp_path, delay=0, session=s)
    assert len(scrape_fda_recalls(tmp_path, fetcher=f)) == 2
    assert s.urls == ["https://www.fda.gov/robots.txt", FDA_RECALLS]
    assert s.headers["User-Agent"].startswith("local-llm-extractor")
    scrape_fda_recalls(tmp_path, fetcher=f)          # second run: served from the disk cache
    assert len(s.urls) == 2


def test_robots_disallow_stops_the_scraper(tmp_path):
    f = PoliteFetcher(tmp_path, delay=0, session=FakeSession("User-agent: *\nDisallow: /safety/\n"))
    with pytest.raises(RobotsDisallowed):
        f.fetch(FDA_RECALLS)


def test_rate_limit_waits_between_requests(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("sources.scraper.time.sleep", sleeps.append)
    f = PoliteFetcher(tmp_path, delay=3.0, session=FakeSession())
    f.fetch(FDA_RECALLS)                               # robots.txt + page = 2 requests
    assert len(sleeps) == 1 and 2.5 < sleeps[0] <= 3.0


def test_crawl_delay_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setattr("sources.scraper.time.sleep", lambda s: None)
    f = PoliteFetcher(tmp_path, delay=1.0, session=FakeSession("User-agent: *\nCrawl-delay: 10\nAllow: /\n"))
    f.fetch(FDA_RECALLS)
    assert f.delay == 10


@pytest.mark.parametrize("component,expected", [
    ("SERVICE BRAKES, HYDRAULIC:FOUNDATION COMPONENTS:DISC:CALIPER", {"brakes"}),
    ("BACK OVER PREVENTION: SENSING SYSTEM: CAMERA", {"visibility_lighting"}),
    ("POWER TRAIN:CLUTCH ASSEMBLY", {"powertrain"}),
    ("AIR BAGS:SIDE/WINDOW:CURTAIN:INFLATOR", {"airbags_seatbelts"}),
    ("SOMETHING NEW", {"other"}),
])
def test_nhtsa_component_mapping(component, expected):
    assert component_categories(component) == expected
