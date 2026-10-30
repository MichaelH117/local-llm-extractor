"""One command: make the PDFs, extract them with a local LLM and with rules, score both, pull the web + API data,
load everything into DuckDB, export Power BI CSVs, and refresh the README results table.

Run: python -m pipeline.run [--seed 42] [--n 50] [--model gemma3:4b] [--offline] [--test]

Nothing is sent to a cloud LLM. The only outbound requests are the FDA page and the NHTSA API (skipped with
--offline, which then uses whatever is already cached).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from extractor import rules
from extractor.classify import FDA_CATEGORIES, FDA_INSTRUCTIONS, NHTSA_INSTRUCTIONS, classify
from extractor.extract import extract_with_llm, hybrid, pdf_text
from extractor.llm import DEFAULT_MODEL, OllamaClient
from generator.make_docs import generate
from pipeline.evaluate import compare, doc_exact, field_scores
from sources import nhtsa
from sources.scraper import scrape_fda_recalls

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DOCS, RAW, CACHE = DATA / "docs", DATA / "raw", DATA / "llm_cache"
DB = DATA / "extractions.duckdb"
EXPORTS = ROOT / "exports"
RESULTS = ROOT / "docs" / "results.json"


def extract_documents(llm: OllamaClient) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Every PDF through the LLM path (retry + fallback) and through the rules. Returns the final table + preds."""
    key = pd.read_csv(DOCS / "answer_key.csv")
    preds = {"rules": {}, "llm": {}, "hybrid": {}}
    rows = []
    for i, k in enumerate(key.itertuples(index=False), 1):
        text = pdf_text(DOCS / f"{k.doc_id}.pdf")
        r = rules.extract(text)
        e = extract_with_llm(text, llm)
        preds["rules"][k.doc_id] = r.model_dump(mode="json")
        preds["llm"][k.doc_id] = e.record.model_dump(mode="json")
        preds["hybrid"][k.doc_id] = hybrid(e.record, r).model_dump(mode="json") if e.method != "rules_fallback" \
            else r.model_dump(mode="json")
        rows.append(dict(doc_id=k.doc_id, layout=k.layout, style=k.style, source_file=f"{k.doc_id}.pdf",
                         **preds["llm"][k.doc_id], extraction_method=e.method, llm_attempts=e.attempts,
                         validation_errors=" | ".join(e.errors) or None, seconds=round(e.seconds, 2),
                         text_chars=len(text)))
        print(f"   {i:>2}/{len(key)} {k.doc_id} {e.method:<14} {e.seconds:5.1f}s", flush=True)
    return pd.DataFrame(rows), preds


def classify_web(llm: OllamaClient, offline: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    stats = {}
    fda_rows, nhtsa_rows = [], []
    try:
        if offline and not any((RAW / "web").glob("*.html")):
            raise RuntimeError("offline and no cached FDA page")
        fda_rows = scrape_fda_recalls(RAW / "web")
    except Exception as e:  # the site can change or block; the document pipeline must still finish
        print(f"   FDA scrape skipped: {e}")
        stats["fda_error"] = str(e)
    for r in fda_rows:
        out, attempts = classify(r["reason"], FDA_CATEGORIES, FDA_INSTRUCTIONS, llm)
        r.update(reason_category=out.category if out else "unknown", hazard_agent=out.agent if out else None,
                 llm_attempts=attempts)
    try:
        if offline and not any((RAW / "api").glob("*.json")):
            raise RuntimeError("offline and no cached NHTSA responses")
        nhtsa_rows = nhtsa.fetch_recalls(RAW / "api")
    except Exception as e:
        print(f"   NHTSA API skipped: {e}")
        stats["nhtsa_error"] = str(e)
    for r in nhtsa_rows:
        truth = nhtsa.component_categories(r["component_nhtsa"])
        out, attempts = classify(f"{r['summary']}\n{r['consequence']}", nhtsa.CATEGORIES, NHTSA_INSTRUCTIONS, llm)
        r.update(category_from_nhtsa=" / ".join(sorted(truth)), category_llm=out.category if out else "unknown",
                 defective_part_llm=out.agent if out else None, match=bool(out and out.category in truth),
                 llm_attempts=attempts)
    fda = pd.DataFrame(fda_rows)
    if not fda.empty:
        fda["recall_date"] = pd.to_datetime(fda.recall_date, format="%m/%d/%Y").dt.date
    return fda, pd.DataFrame(nhtsa_rows), stats


def load_and_export(tables: dict[str, pd.DataFrame]) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(exist_ok=True)
    with duckdb.connect(str(DB)) as con:
        for name, df in tables.items():
            con.register("df_view", df)
            con.execute(f"create or replace table {name} as select * from df_view")
            con.unregister("df_view")
    for name, df in tables.items():
        df.to_csv(EXPORTS / f"{name}.csv", index=False, encoding="utf-8")


def pct(x) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:.1f}%"


_COUNT = re.compile(r"\b(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b")


def parse_pytest(stdout: str, returncode: int) -> dict:
    """Turn pytest -q output into counts. ok is False when the suite did not run cleanly: any error (for example a
    collection error), an exit code other than 0 (all passed) or 1 (some failed), or no summary line at all. Without
    this, a suite that never ran would be recorded as "0 passed, 0 failed"."""
    lines = stdout.strip().splitlines()
    tail = next((ln.strip("= ") for ln in reversed(lines) if _COUNT.search(ln)), "")
    found = {k: int(n) for n, k in _COUNT.findall(tail)}
    counts = {k: found.get(k, 0) for k in ("passed", "failed", "skipped", "xfailed", "xpassed")}
    counts["errors"] = found.get("error", 0) + found.get("errors", 0)
    ok = bool(tail) and counts["errors"] == 0 and returncode in (0, 1)
    return {**counts, "returncode": returncode, "ok": ok, "summary": tail or "no pytest summary line",
            "date": date.today().isoformat()}


def run_pytest() -> dict:
    """Run the test suite (the integration test calls the local model if Ollama is up, else it is skipped)."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True)
    print(proc.stdout[-3000:])
    t = parse_pytest(proc.stdout, proc.returncode)
    if not t["ok"]:
        print(proc.stderr[-2000:])
    return t


def pytest_row(t, recorded: str) -> tuple[str, str, str]:
    """README row for the pytest result. A run that did not complete cleanly says so instead of '0 failed'."""
    if not t:
        return ("pytest", "**not run**", "run with --test to record it")
    if isinstance(t, str):  # results.json written before counts were structured
        return ("pytest", f"**{t}**", f"recorded {recorded}")
    extra = "".join(f", {t[k]} {k}" for k in ("skipped", "xfailed", "xpassed") if t.get(k))
    if t.get("ok") is False:
        return ("pytest", "**DID NOT RUN CLEANLY**", f"{t.get('errors', 0)} errors, pytest exit code "
                f"{t.get('returncode', '?')} ({t.get('summary', '')}); counts are not valid; "
                f"recorded {t.get('date', recorded)}")
    return ("pytest", f"**{t['passed']} passed, {t['failed']} failed**",
            f"{t['errors']} errors{extra}; recorded {t.get('date', recorded)}")


def update_readme(res: dict) -> None:
    s = res["scores"]
    rows = [
        ("Documents (SYNTHETIC)", f"**{res['n_docs']} PDFs**", "invented by a seeded generator; "
         "3 layouts (form, table, free-text email); 25 seen + 25 unseen "
         "templates; answer key never read by the extractors"),
        ("LLM field precision / recall, all docs", f"**{pct(s['llm']['all']['precision'])} / "
         f"{pct(s['llm']['all']['recall'])}**", f"{res['model']} on a laptop CPU, 9 fields x {res['n_docs']} docs"),
        ("Rules field precision / recall, all docs", f"**{pct(s['rules']['all']['precision'])} / "
         f"{pct(s['rules']['all']['recall'])}**", "regex + keyword baseline written while looking at the seen docs"),
        ("Unseen templates: LLM vs rules recall", f"**{pct(s['llm']['unseen']['recall'])} vs "
         f"{pct(s['rules']['unseen']['recall'])}**", "new labels, synonyms and formats neither was tuned on"),
        ("Seen templates: LLM vs rules recall", f"**{pct(s['llm']['seen']['recall'])} vs "
         f"{pct(s['rules']['seen']['recall'])}**", "the rules' home ground"),
        ("Hybrid (LLM, nulls filled by rules) recall", f"**{pct(s['hybrid']['all']['recall'])}**",
         f"precision {pct(s['hybrid']['all']['precision'])}"),
        ("Docs with all 9 fields right (LLM)", f"**{pct(res['docs_all_correct']['llm'])}**",
         f"rules {pct(res['docs_all_correct']['rules'])}, hybrid {pct(res['docs_all_correct']['hybrid'])}"),
        ("Invalid LLM answers", f"**{res['llm_retried']} retried, {res['llm_fallback']} fell back to rules**",
         "pydantic rejected the JSON; the error was sent back to the model"),
        ("LLM time per real model call",
         f"**{res['llm_seconds'] / res['llm_calls']:.1f} s average**" if res["llm_calls"] else "n/a (all cached)",
         f"{res['llm_calls']} uncached calls in {res['llm_seconds']:.0f} s; CPU only (Intel Iris Xe, no CUDA GPU), "
         "temperature 0; cached answers take ~0 s"),
        ("NHTSA recalls (public API): LLM category = NHTSA component",
         f"**{res['nhtsa']['matched']} of {res['nhtsa']['n']}**" if res["nhtsa"]["n"] else "n/a",
         "public data: real free-text summaries; NHTSA's own component label is the answer key"),
        ("FDA recalls scraped and classified", f"**{res['fda']['n']} rows**",
         "public data; robots.txt checked, 1 page, cached; no answer key (hand spot-check below)"),
        pytest_row(res.get("pytest"), res["date"]),
    ]
    block = "| Result | Value | What is behind it |\n|---|---|---|\n"
    block += "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    note = (f"\nNumbers from `python -m pipeline.run --seed {res['seed']} --test` ({res['run_seconds']:.0f} s run, "
            f"{res['llm_calls']} real model calls), stored in [docs/results.json](docs/results.json).\n")
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    new = re.sub(r"<!-- results:start -->.*<!-- results:end -->",
                 lambda _: f"<!-- results:start -->\n{block}\n{note}<!-- results:end -->", text, flags=re.S)
    readme.write_text(new, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--offline", action="store_true", help="no web requests; use cached FDA/NHTSA data if present")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached LLM answers and call the model again")
    ap.add_argument("--test", action="store_true", help="also run pytest and record the result in the README")
    a = ap.parse_args()
    t0 = time.perf_counter()

    llm = OllamaClient(a.ollama_url, a.model, cache_dir=None if a.no_cache else CACHE)
    if not llm.available():
        raise SystemExit(f"Ollama is not running at {a.ollama_url} or model {a.model} is missing. "
                         f"Start Ollama and run: ollama pull {a.model}")

    print(f"1/5 generate {a.n} synthetic work-order PDFs ...")
    generate(a.seed, a.n, DOCS)
    print(f"2/5 extract (local {a.model} + rules) ...")
    orders, preds = extract_documents(llm)
    print("3/5 score against the answer key ...")
    key = pd.read_csv(DOCS / "answer_key.csv")
    cmp = pd.concat([compare(key, p, m) for m, p in preds.items()], ignore_index=True)
    scores, exact = field_scores(cmp), doc_exact(cmp)
    print("4/5 FDA recalls page (scraped) + NHTSA recalls API, classified by the local model ...")
    fda, nh, web_stats = classify_web(llm, a.offline)
    print("5/5 DuckDB + Power BI CSVs ...")
    errors = cmp[~cmp.outcome.isin(["correct", "correct_null"])].copy()
    for col in ("expected", "extracted"):
        errors[col] = errors[col].astype("string")
    load_and_export({"work_orders": orders, "field_accuracy": scores, "extraction_errors": errors,
                     "fda_recalls": fda, "nhtsa_recalls": nh})

    allf = scores[scores.field == "ALL FIELDS"]
    res = {
        "seed": a.seed, "n_docs": len(orders), "model": a.model, "date": date.today().isoformat(),
        "scores": {m: {st: {"precision": float(r.precision), "recall": float(r.recall), "f1": float(r.f1)}
                       for r in allf[allf.method == m].itertuples() for st in [r.style]} for m in preds},
        "docs_all_correct": {r.method: float(r.docs_all_correct) for r in exact[exact["style"] == "all"].itertuples()},
        "llm_first_try": int((orders.extraction_method == "llm").sum()),
        "llm_retried": int((orders.extraction_method == "llm_retry").sum()),
        "llm_fallback": int((orders.extraction_method == "rules_fallback").sum()),
        "sec_per_doc": round(float(orders.seconds.median()), 1),
        "llm_calls": llm.calls, "llm_seconds": round(llm.seconds, 1),
        "nhtsa": {"n": len(nh), "matched": int(nh.match.sum()) if len(nh) else 0},
        "fda": {"n": len(fda), "categories": fda.reason_category.value_counts().to_dict() if len(fda) else {}},
        **web_stats,
    }
    if a.test:
        res["pytest"] = run_pytest()
    res["run_seconds"] = round(time.perf_counter() - t0, 1)
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(res, indent=2), encoding="utf-8")
    update_readme(res)
    s = res["scores"]
    print(f"\nLLM   P/R {pct(s['llm']['all']['precision'])} / {pct(s['llm']['all']['recall'])}   "
          f"rules P/R {pct(s['rules']['all']['precision'])} / {pct(s['rules']['all']['recall'])}   "
          f"NHTSA {res['nhtsa']['matched']}/{res['nhtsa']['n']}   {res['run_seconds']:.0f}s")
    if a.test and not res["pytest"]["ok"]:
        raise SystemExit(f"pytest did not run cleanly ({res['pytest']['summary']}); README marks it as not valid")


if __name__ == "__main__":
    main()
