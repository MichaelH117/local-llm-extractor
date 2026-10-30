"""Precompute everything the Quarto site shows, from the pipeline's real outputs (design-lead seat).

Runs automatically as Quarto's pre-render step (`quarto render site`), or by hand from the repo root:

    .venv\\Scripts\\python site\\build_data.py

Reads (never writes) the run outputs:
  docs/results.json, exports/field_accuracy.csv, exports/extraction_errors.csv, exports/work_orders.csv,
  exports/nhtsa_recalls.csv, exports/fda_recalls.csv, and (when present) the SYNTHETIC PDFs + answer key in
  data/docs/ and the cached model answers in data/llm_cache/ (both git-ignored, rebuilt by the generator/pipeline).

Writes: site/_variables.yml (every number quoted in the prose), site/_gen/*.html (static charts and tables),
site/data/site-data.js (the document reader's data), site/pdf/*.pdf (the 50 SYNTHETIC PDFs), site/assets/brand/.

THE BUILD FAILS (exit 1) if any number disagrees between results.json, the CSV exports and a recount from the
answer key, or if a headline the prose relies on has changed. Fix the prose, then update HEADLINE below.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent

# ---- run under the project venv (pypdf + the generator's label lists live there) -------------------------------
try:
    import pypdf  # noqa: F401
except ImportError:
    for cand in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if cand.exists() and Path(sys.executable).resolve() != cand.resolve():
            sys.exit(subprocess.call([str(cand), str(Path(__file__).resolve()), *sys.argv[1:]]))
    sys.exit("build_data: pypdf not found. Create the venv first (see README), or set QUARTO_PYTHON to it.")

from pypdf import PdfReader  # noqa: E402

sys.path.insert(0, str(ROOT))
from generator.make_docs import STYLES  # noqa: E402  (the same label lists the PDFs were made from)

FIELDS = ["work_order_id", "date_reported", "equipment_type", "serial_number", "failure_category",
          "labor_hours", "parts_cost_usd", "priority", "warranty_claim"]
NUMERIC = {"labor_hours", "parts_cost_usd"}
PLAIN = {"work_order_id": "Work order number", "date_reported": "Date reported", "equipment_type": "Equipment",
         "serial_number": "Serial number", "failure_category": "What broke", "labor_hours": "Labor hours",
         "parts_cost_usd": "Parts cost", "priority": "Priority", "warranty_claim": "Warranty claim"}

# The prose on the pages quotes these. If a new run changes them, the build stops so the words get re-checked.
HEADLINE = {
    "llm_unseen_p": 94.0, "llm_unseen_r": 91.9, "llm_seen_p": 97.3, "llm_seen_r": 98.6, "rules_unseen_r": 41.0,
    "llm_wrong_cells": 24, "cells": 450, "invented_numbers": 0, "invented_dates": 3, "nhtsa": (31, 35),
    "causes": {"priority": 9, "category": 7, "date": 3, "fallback": 5},
}
FEATURED = [  # doc, the claim its caption makes (checked below)
    ("doc_003", "familiar form: AI and rules both 9 of 9"),
    ("doc_026", "new free-text note: AI 9 of 9, rules 3 of 9"),
    ("doc_046", "new table: priority 'P3 - Deferred' read as high"),
    ("doc_002", "familiar email: AI invented a report date"),
]

FAIL: list[str] = []


def check(ok: bool, msg: str) -> None:
    if not ok:
        FAIL.append(msg)


def read_csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pct(x: float) -> float:
    return round(100 * x + 1e-9, 1)


# ================================================================== inputs
results = json.loads((ROOT / "docs" / "results.json").read_text(encoding="utf-8"))
acc = read_csv(ROOT / "exports" / "field_accuracy.csv")
errs = read_csv(ROOT / "exports" / "extraction_errors.csv")
orders = {r["doc_id"]: r for r in read_csv(ROOT / "exports" / "work_orders.csv")}
nhtsa = read_csv(ROOT / "exports" / "nhtsa_recalls.csv")
fda = read_csv(ROOT / "exports" / "fda_recalls.csv")
KEY_PATH = ROOT / "data" / "docs" / "answer_key.csv"
HAVE_DOCS = KEY_PATH.exists()
key = {r["doc_id"]: r for r in read_csv(KEY_PATH)} if HAVE_DOCS else {}

A = {(r["method"], r["style"], r["field"]): r for r in acc}


def score(method: str, style: str, field: str = "ALL FIELDS") -> dict:
    r = A[(method, style, field)]
    out = {k: int(r[k]) for k in ("tp", "fp", "fn", "n_cells")}
    out["p"] = float(r["precision"]) if r["precision"] else None
    out["r"] = float(r["recall"]) if r["recall"] else None
    return out


# ================================================================== check 1: results.json == field_accuracy.csv
for m in ("llm", "rules", "hybrid"):
    for s in ("all", "seen", "unseen"):
        j, c = results["scores"][m][s], score(m, s)
        check(abs(j["precision"] - c["p"]) < 1e-4 and abs(j["recall"] - c["r"]) < 1e-4,
              f"results.json {m}/{s} P/R {j['precision']}/{j['recall']} != field_accuracy.csv {c['p']}/{c['r']}")
        check(abs(c["tp"] / (c["tp"] + c["fp"]) - c["p"]) < 1e-4 and abs(c["tp"] / (c["tp"] + c["fn"]) - c["r"]) < 1e-4,
              f"field_accuracy.csv {m}/{s}: tp/fp/fn do not give its own P/R")

# ================================================================== check 2: error rows agree with tp/fp/fn
E = defaultdict(list)
for r in errs:
    E[r["method"]].append(r)
for m in ("llm", "rules", "hybrid"):
    for s in ("all", "seen", "unseen"):
        for f in FIELDS + ["ALL FIELDS"]:
            rows = [r for r in E[m] if (s == "all" or r["style"] == s) and (f == "ALL FIELDS" or r["field"] == f)]
            oc = Counter(r["outcome"] for r in rows)
            c = score(m, s, f)
            check(c["fp"] == oc["wrong"] + oc["hallucinated"] and c["fn"] == oc["wrong"] + oc["missed"],
                  f"extraction_errors.csv vs field_accuracy.csv disagree on fp/fn for {m}/{s}/{f}")


# ================================================================== check 3: recount from the answer key
def truth(f, raw):
    if raw in (None, ""):
        return None
    if f in NUMERIC:
        return float(raw)
    if f == "warranty_claim":
        return str(raw) == "True"
    return str(raw)


def pred(f, raw):
    if raw in (None, ""):
        return None
    if f in NUMERIC:
        return float(raw)
    if f == "warranty_claim":
        return str(raw) == "True"
    return str(raw)


def same(f, t, p):
    if t is None or p is None:
        return t is None and p is None
    if f in NUMERIC:
        return abs(t - p) < 0.01
    return str(t).strip().lower() == str(p).strip().lower()


def outcome(t, p, ok):
    if ok:
        return "correct" if t is not None else "correct_null"
    return "missed" if p is None else "hallucinated" if t is None else "wrong"


err_idx = {(r["method"], r["doc_id"], r["field"]): r for r in errs}
if HAVE_DOCS:
    check(sorted(key) == sorted(orders), "answer key and work_orders.csv list different documents")
    # the exported work orders ARE the LLM's final answers (doc_043: the rules fallback); recount every cell
    recount = {}
    for d, k in key.items():
        for f in FIELDS:
            t, p = truth(f, k[f]), pred(f, orders[d][f])
            recount[(d, f)] = outcome(t, p, same(f, t, p))
    bad = {(d, f): o for (d, f), o in recount.items() if o not in ("correct", "correct_null")}
    listed = {(r["doc_id"], r["field"]): r["outcome"] for r in E["llm"]}
    check(bad == listed, f"work_orders.csv vs answer key gives {len(bad)} wrong LLM cells; extraction_errors.csv lists {len(listed)}")
    for m in ("llm", "rules"):
        for s in ("all", "seen", "unseen"):
            tp = sum(1 for d, k in key.items() for f in FIELDS
                     if (s == "all" or k["style"] == s) and k[f] != "" and (m, d, f) not in err_idx)
            check(tp == score(m, s)["tp"], f"recount from answer key: {m}/{s} tp {tp} != {score(m, s)['tp']}")

# ================================================================== headline guard
llm_u, llm_s, rul_u, rul_s = score("llm", "unseen"), score("llm", "seen"), score("rules", "unseen"), score("rules", "seen")
llm_a, rul_a = score("llm", "all"), score("rules", "all")
oc_llm = Counter(r["outcome"] for r in E["llm"])
inv_num = sum(1 for r in E["llm"] if r["field"] in NUMERIC and r["outcome"] in ("wrong", "hallucinated"))
inv_dates = sum(1 for r in E["llm"] if r["field"] == "date_reported" and r["outcome"] == "hallucinated")
nh_match = sum(1 for r in nhtsa if r["match"] == "True")


def cause(r: dict) -> str:
    if r["doc_id"] == "doc_043" and r["outcome"] == "missed":
        return "fallback"
    if r["field"] == "priority":
        return "priority"
    if r["field"] == "failure_category":
        return "category"
    if r["field"] == "date_reported" and r["outcome"] == "hallucinated":
        return "date"
    return "other"


causes = Counter(cause(r) for r in E["llm"])
got = {
    "llm_unseen_p": pct(llm_u["p"]), "llm_unseen_r": pct(llm_u["r"]), "llm_seen_p": pct(llm_s["p"]),
    "llm_seen_r": pct(llm_s["r"]), "rules_unseen_r": pct(rul_u["r"]), "llm_wrong_cells": len(E["llm"]),
    "cells": llm_a["n_cells"], "invented_numbers": inv_num, "invented_dates": inv_dates,
    "nhtsa": (nh_match, len(nhtsa)), "causes": {k: causes[k] for k in HEADLINE["causes"]},
}
for k, v in HEADLINE.items():
    check(got[k] == v, f"headline {k}: the run says {got[k]}, the pages say {v}. Re-check the prose, then update HEADLINE.")
check(causes["other"] == 0, f"{causes['other']} LLM error cells fit none of the 4 causes the error gallery describes")
check((results["nhtsa"]["matched"], results["nhtsa"]["n"]) == (nh_match, len(nhtsa)), "results.json NHTSA != nhtsa_recalls.csv")
check(results["fda"]["n"] == len(fda), "results.json FDA count != fda_recalls.csv")
check(dict(Counter(r["reason_category"] for r in fda)) == results["fda"]["categories"], "FDA categories disagree")
check(results["n_docs"] == len(orders), "results.json n_docs != work_orders.csv rows")

# ================================================================== documents: text, highlight spans, per-field verdicts
LABEL2F: dict[str, str] = {}
for st in STYLES.values():
    for f, labs in st["labels"].items():
        for lab in labs:
            LABEL2F[lab] = f
for lab, f in {"WO#": "work_order_id", "S/N": "serial_number", "Hrs": "labor_hours", "Parts $": "parts_cost_usd",
               "Wrnty": "warranty_claim", "Pri": "priority"}.items():
    LABEL2F[lab] = f
LABELS_LONGEST = sorted(LABEL2F, key=len, reverse=True)


def phrase_re(s: str) -> str:
    return r"\s+".join(re.escape(w) for w in s.split())


def find(text: str, pattern: str, group: int = 0, flags=re.I):
    m = re.search(pattern, text, flags)
    return (m.start(group), m.end(group)) if m else None


def spans_for(doc: dict, text: str) -> list[dict]:
    """Where each answer sits in the text. Found by THIS script from the generator's label lists, not by the AI."""
    out = []
    add = lambda se, f, kind="field": se and out.append({"s": se[0], "e": se[1], "f": f, "k": kind})  # noqa: E731
    if doc["layout"] in ("form", "table"):
        lines, pos = text.split("\n"), 0
        offs = []
        for ln in lines:
            offs.append(pos)
            pos += len(ln) + 1
        for i, ln in enumerate(lines):
            st = ln.strip()
            if doc["layout"] == "table" and st in LABEL2F and i + 1 < len(lines):
                v = lines[i + 1]
                add((offs[i + 1], offs[i + 1] + len(v.rstrip())), LABEL2F[st])
            elif doc["layout"] == "form":
                for lab in LABELS_LONGEST:
                    if st.startswith(lab + ":"):
                        a = offs[i] + ln.index(lab + ":") + len(lab) + 1
                        a += len(ln[a - offs[i]:]) - len(ln[a - offs[i]:].lstrip())
                        add((a, offs[i] + len(ln.rstrip())), LABEL2F[lab])
                        break
    else:
        s = STYLES[doc["style"]]
        k = key.get(doc["id"], {})
        add(find(text, re.escape(k.get("work_order_id", "~~"))), "work_order_id")
        sn = k.get("serial_number", "~~")
        add(find(text, re.escape(sn)) or find(text, phrase_re(sn.replace("-", " "))), "serial_number")
        add(find(text, phrase_re(k.get("equipment_type", "~~"))), "equipment_type")
        add(find(text, r"came in on ([^\n]+?)\.\s", 1) or find(text, r"Shift log, bay \d+, ([^\n]+)", 1), "date_reported")
        add(find(text, r"last PM was ([^\n]+?)\.\s", 1) or find(text, r"Previous\s+service\s+([^,]+),", 1), "date_reported", "trap")
        add(find(text, r"(Meter shows [\d,]+ hrs)", 1) or find(text, r"(hour meter at \d+)", 1), "labor_hours", "trap")
        add(find(text, r"Took\s+about\s+([\d.]+\s+hours)", 1) or find(text, r"Spent\s+(.+?)\s+on\s+it", 1), "labor_hours")
        add(find(text, r"parts\s+came\s+to\s+((?:USD\s+)?\$?\s?[\d,]+\.\d\d(?:\s+USD)?)", 1)
            or find(text, r"material\s+((?:\$\s)?[\d,]+\.\d\d(?:\s+dollars|\s+\(USD\))?)", 1), "parts_cost_usd")
        for f, src in (("failure_category", s["symptoms"]), ("warranty_claim", s["warranty"]), ("priority", s["priority"])):
            for phrases in src.values():
                for ph in phrases:
                    se = find(text, phrase_re(ph))
                    if se:
                        add(se, f)
    out.sort(key=lambda x: x["s"])
    keep, end = [], -1
    for sp in out:  # drop overlaps (keep the first)
        if sp["s"] >= end:
            keep.append(sp)
            end = sp["e"]
    return keep


def show(f: str, v) -> str:
    if v is None:
        return ""
    if f == "warranty_claim":
        return "Yes" if v else "No"
    if f == "failure_category":
        return str(v).replace("_", " / ")
    if f == "labor_hours":
        return f"{v:g}"
    if f == "parts_cost_usd":
        return f"${v:,.2f}"
    return str(v)


docs_out = []
prev_docs = {}
prev_js = SITE / "data" / "site-data.js"
if prev_js.exists():
    txt = prev_js.read_text(encoding="utf-8")
    prev_docs = {d["id"]: d for d in json.loads(txt[txt.index("=") + 1:].rstrip().rstrip(";"))["docs"]}

if HAVE_DOCS:
    for d, k in key.items():
        text = PdfReader(ROOT / "data" / "docs" / f"{d}.pdf").pages[0].extract_text().strip()
        o = orders[d]
        doc = {"id": d, "layout": k["layout"], "style": k["style"], "text": text,
               "method": o["extraction_method"], "attempts": int(o["llm_attempts"] or 0)}
        rows = []
        for f in FIELDS:
            t = truth(f, k[f])
            ai = pred(f, o[f])
            er = err_idx.get(("rules", d, f))
            rv = pred(f, er["extracted"]) if er else t
            rows.append({"f": f, "key": show(f, t), "ai": show(f, ai), "aio": recount[(d, f)],
                         "rules": show(f, rv), "ro": er["outcome"] if er else ("correct" if t is not None else "correct_null")})
        doc["rows"] = rows
        doc["spans"] = spans_for(doc, text)
        missing = {f for f in FIELDS if k[f] != ""} - {sp["f"] for sp in doc["spans"] if sp["k"] == "field"}
        doc["unlocated"] = sorted(missing)
        docs_out.append(doc)
else:
    print("build_data: data/docs/ not found (git-ignored). Keeping the committed document data; run "
          "`python -m generator.make_docs` to rebuild the PDFs and answer key for a full re-check.")
    docs_out = list(prev_docs.values())
    check(bool(docs_out), "no document data: run the generator first")

DOCS = {d["id"]: d for d in docs_out}
ok_count = lambda d, who: sum(r[who + "o"] in ("correct", "correct_null") if who == "ai" else r["ro"] in ("correct", "correct_null") for r in d["rows"])  # noqa: E731
# featured captions must stay true
c = {d: (ok_count(DOCS[d], "ai"), ok_count(DOCS[d], "r")) for d, _ in FEATURED}
check(c["doc_003"] == (9, 9), f"doc_003 caption: AI/rules {c['doc_003']}")
check(c["doc_026"] == (9, 3), f"doc_026 caption: AI/rules {c['doc_026']}")
r46 = {r["f"]: r for r in DOCS["doc_046"]["rows"]}["priority"]
check(r46["ai"] == "high" and r46["key"] == "low", "doc_046 caption: priority")
r02 = {r["f"]: r for r in DOCS["doc_002"]["rows"]}["date_reported"]
check(r02["aio"] == "hallucinated", "doc_002 caption: invented date")
n_unloc = sum(len(d["unlocated"]) for d in docs_out)

# doc_043's three answers, from the model cache (git-ignored; optional)
attempts_043 = []
cache = ROOT / "data" / "llm_cache"
if cache.exists():
    for p in cache.glob("*.json"):
        c2 = json.loads(p.read_text(encoding="utf-8"))
        if "WO-2025-86556" in c2.get("content", ""):
            attempts_043.append(json.loads(c2["content"])["date_reported"])
    check(sorted(attempts_043) == ["19-07-2025", "19-07-2025", "19-JUL-2025"],
          f"doc_043 cached date answers changed: {attempts_043}")

# smaller claims the error gallery makes in words
check(sum(1 for r in E["llm"] if r["field"] == "failure_category" and r["extracted"] == "engine") == 6,
      "errors page says 6 of the 7 wrong 'what broke' answers were engine")
if cache.exists():
    c043 = next(json.loads(json.loads(p.read_text(encoding="utf-8"))["content"]) for p in cache.glob("*.json")
                if "WO-2025-86556" in p.read_text(encoding="utf-8"))
    lost = [r for r in E["llm"] if r["doc_id"] == "doc_043"]
    right_lost = sum(1 for r in lost if same(r["field"], truth(r["field"], r["expected"]), pred(r["field"], str(c043[r["field"]]) if c043[r["field"]] is not None else "")))
    check(right_lost == 3, f"errors page says 3 of doc_043's 5 lost boxes were right in the AI's answer (now {right_lost})")

# ================================================================== fail now if anything disagreed
if FAIL:
    print("build_data: NUMBERS DO NOT MATCH THE EXPORTS. The site was not rebuilt.", file=sys.stderr)
    for m in FAIL:
        print("  - " + m, file=sys.stderr)
    sys.exit(1)

# ================================================================== variables for the prose
pytest_s = results["pytest"] if isinstance(results["pytest"], str) else \
    f"{results['pytest'].get('passed', 0)} passed, {results['pytest'].get('failed', 0)} failed"
docs_ok = {m: round(results["docs_all_correct"][m] * results["n_docs"]) for m in ("llm", "rules")}
num_cells = 2 * results["n_docs"]
num_missed = sum(1 for r in E["llm"] if r["field"] in NUMERIC)
fl = lambda x: f"{x:.1f}"  # noqa: E731
V = {
    "run": {"date": results["date"], "model": results["model"], "docs": results["n_docs"], "pytest": pytest_s,
            "seconds": round(results["run_seconds"]), "calls": results["llm_calls"],
            "sec_per_call": fl(results["llm_seconds"] / results["llm_calls"]),
            "first_try": results["llm_first_try"], "retried": results["llm_retried"], "fallback": results["llm_fallback"]},
    "cells": llm_a["n_cells"], "cells_half": llm_u["n_cells"], "wrong": len(E["llm"]), "right": llm_a["n_cells"] - len(E["llm"]),
    "wrong_vals": oc_llm["wrong"], "invented": oc_llm["hallucinated"], "blanks": oc_llm["missed"],
    "llm": {"u_p": fl(pct(llm_u["p"])), "u_r": fl(pct(llm_u["r"])), "s_p": fl(pct(llm_s["p"])), "s_r": fl(pct(llm_s["r"])),
            "a_p": fl(pct(llm_a["p"])), "a_r": fl(pct(llm_a["r"])),
            "u_tp": llm_u["tp"], "u_facts": llm_u["tp"] + llm_u["fn"], "u_answers": llm_u["tp"] + llm_u["fp"],
            "s_tp": llm_s["tp"], "s_facts": llm_s["tp"] + llm_s["fn"],
            "u_wrong": sum(1 for r in E["llm"] if r["style"] == "unseen" and r["outcome"] == "wrong"),
            "u_blank": sum(1 for r in E["llm"] if r["style"] == "unseen" and r["outcome"] == "missed"),
            "docs_ok": docs_ok["llm"], "drop": fl(pct(llm_s["r"]) - pct(llm_u["r"]))},
    "rules": {"u_p": fl(pct(rul_u["p"])), "u_r": fl(pct(rul_u["r"])), "s_p": fl(pct(rul_s["p"])), "s_r": fl(pct(rul_s["r"])),
              "u_tp": rul_u["tp"], "u_facts": rul_u["tp"] + rul_u["fn"], "docs_ok": docs_ok["rules"],
              "u_wrong": sum(1 for r in E["rules"] if r["style"] == "unseen" and r["outcome"] == "wrong"),
              "u_blank": sum(1 for r in E["rules"] if r["style"] == "unseen" and r["outcome"] == "missed"),
              "drop": fl(pct(rul_s["r"]) - pct(rul_u["r"]))},
    "num": {"cells": num_cells, "invented": inv_num, "blank": num_missed},
    "dates_invented": inv_dates,
    "causes": dict(causes),
    "nhtsa": {"match": nh_match, "n": len(nhtsa), "miss": len(nhtsa) - nh_match},
    "fda": {"n": len(fda)},
    "unlocated": n_unloc,
}


def yml(d: dict, ind: int = 0) -> str:
    s = ""
    for k, v in d.items():
        if isinstance(v, dict):
            s += " " * ind + f"{k}:\n" + yml(v, ind + 2)
        else:
            s += " " * ind + f'{k}: "{v}"\n'
    return s


(SITE / "_variables.yml").write_text("# GENERATED by site/build_data.py from the run outputs. Do not edit.\n" + yml(V), encoding="utf-8")

# ================================================================== static charts and tables (site/_gen/*.html)
GEN = SITE / "_gen"
GEN.mkdir(exist_ok=True)
esc = html.escape


def put(name: str, body: str) -> None:
    (GEN / name).write_text("```{=html}\n" + body.strip() + "\n```\n", encoding="utf-8")


# --- familiar vs new forms: 4 bars (recall = facts found), then per-field dumbbells
def bar(label, val, n_ok, n, cls):
    return (f'<div class="bar-row {cls}"><span class="bar-label">{label}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{val}%"></span></span>'
            f'<span class="bar-val"><b>{val:.1f}%</b> <span class="bar-n">{n_ok} of {n}</span></span></div>')


su = (f'<figure class="chart" aria-labelledby="cap-su"><figcaption id="cap-su" class="chart-cap">Share of the facts each '
      f'method found, out of 100%. Bars start at zero. n = facts in the answer key for that half.</figcaption>'
      f'<div class="bars" role="list">'
      f'<div class="bar-group" role="listitem"><h4 class="bar-group-h">Familiar forms (25 documents)</h4>'
      + bar("AI", pct(llm_s["r"]), llm_s["tp"], llm_s["tp"] + llm_s["fn"], "is-ai")
      + bar("Plain rules", pct(rul_s["r"]), rul_s["tp"], rul_s["tp"] + rul_s["fn"], "is-rules")
      + f'</div><div class="bar-group" role="listitem"><h4 class="bar-group-h">New forms it had never seen (25 documents)</h4>'
      + bar("AI", pct(llm_u["r"]), llm_u["tp"], llm_u["tp"] + llm_u["fn"], "is-ai")
      + bar("Plain rules", pct(rul_u["r"]), rul_u["tp"], rul_u["tp"] + rul_u["fn"], "is-rules")
      + "</div></div></figure>")
put("seen_unseen.html", su)

rows = []
for f in FIELDS:
    cells = []
    for m in ("llm", "rules"):
        for s in ("seen", "unseen"):
            c3 = score(m, s, f)
            cells.append(c3)
    rows.append((f, cells))


def p_or_na(x):
    return "n/a" if x is None else f"{pct(x):.0f}%"


tbl = ['<div class="table-wrap" tabindex="0" role="region" aria-labelledby="cap-field"><table data-quarto-disable-processing="true" class="data">'
       '<caption id="cap-field">Each of the 9 boxes: how often the answer was right when given (precision) and how many '
       'of the real values were found (recall). "n/a" = the method gave no answer at all. 25 documents per half.</caption>'
       '<thead><tr><th scope="col">Box</th><th scope="col">AI, familiar: right / found</th><th scope="col">Rules, familiar</th>'
       '<th scope="col">AI, new: right / found</th><th scope="col">Rules, new</th></tr></thead><tbody>']
for f, (ls, lu, rs, ru) in rows:
    tbl.append(f'<tr><th scope="row">{PLAIN[f]}</th>' + "".join(
        f'<td class="num">{p_or_na(c4["p"])} / {p_or_na(c4["r"])}</td>' for c4 in (ls, rs, lu, ru)) + "</tr>")
tbl.append(f'<tr class="total"><th scope="row">All 9 boxes</th>' + "".join(
    f'<td class="num"><b>{pct(c4["p"]):.1f}% / {pct(c4["r"]):.1f}%</b></td>' for c4 in (llm_s, rul_s, llm_u, rul_u)) + "</tr>")
tbl.append("</tbody></table></div>")
put("field_table.html", "".join(tbl))

# dumbbells: AI recall familiar -> new, per field
db = ['<figure class="chart dumbbells" aria-labelledby="cap-db"><figcaption id="cap-db" class="chart-cap">Facts found per box, '
      'familiar forms (hollow ring) vs new forms (solid dot). Scale 0 to 100%. The table below has every number.</figcaption>'
      '<div class="db-legend" aria-hidden="true"><span><i class="db-ring"></i> familiar forms</span><span><i class="db-dot"></i> new forms</span></div>']
for m, name in (("llm", "AI"), ("rules", "Plain rules")):
    db.append(f'<div class="db-block is-{"ai" if m == "llm" else "rules"}"><h4 class="bar-group-h">{name}</h4><ul class="db-list">')
    for f in FIELDS:
        a, b = score(m, "seen", f)["r"] or 0, score(m, "unseen", f)["r"] or 0
        lo, hi = sorted((a, b))
        db.append(f'<li class="db-row"><span class="db-label">{PLAIN[f]}</span><span class="db-track" aria-hidden="true">'
                  f'<span class="db-line" style="left:{100 * lo:.1f}%;width:{100 * (hi - lo):.1f}%"></span>'
                  f'<span class="db-ring" style="left:{100 * a:.1f}%"></span><span class="db-dot" style="left:{100 * b:.1f}%"></span></span>'
                  f'<span class="db-val">{100 * a:.0f}% → <b>{100 * b:.0f}%</b></span></li>')
    db.append("</ul></div>")
db.append("</figure>")
put("dumbbells.html", "".join(db))


# --- unit chart: every fact on the new forms, one square each
def units(m: str) -> str:
    rws = [r for r in E[m] if r["style"] == "unseen"]
    wrong = sum(1 for r in rws if r["outcome"] == "wrong" and r["expected"] != "")
    blank = sum(1 for r in rws if r["outcome"] == "missed")
    tp = score(m, "unseen")["tp"]
    n = tp + wrong + blank
    assert n == score(m, "unseen")["tp"] + score(m, "unseen")["fn"], "unit chart counts"
    cols, sz, gap = 24, 10, 3
    rows_n = -(-n // cols)
    w, h = cols * (sz + gap) - gap, rows_n * (sz + gap) - gap
    sq = []
    for i in range(n):
        x, y = (i % cols) * (sz + gap), (i // cols) * (sz + gap)
        kind = "u-found" if i < tp else "u-wrong" if i < tp + wrong else "u-blank"
        if kind == "u-wrong":
            sq.append(f'<g class="{kind}"><rect x="{x + .75}" y="{y + .75}" width="{sz - 1.5}" height="{sz - 1.5}"/>'
                      f'<path d="M{x + 2} {y + 2}L{x + sz - 2} {y + sz - 2}M{x + sz - 2} {y + 2}L{x + 2} {y + sz - 2}"/></g>')
        else:
            sq.append(f'<rect class="{kind}" x="{x + .75}" y="{y + .75}" width="{sz - 1.5}" height="{sz - 1.5}"/>')
    name = "The AI" if m == "llm" else "Plain rules"
    return (f'<div class="unit-block is-{"ai" if m == "llm" else "rules"}"><h4 class="bar-group-h">{name}: found '
            f'<b>{tp}</b> of {n}</h4><svg class="units" viewBox="0 0 {w} {h}" role="img" aria-label="{name}: {tp} found, '
            f'{wrong} wrong, {blank} left blank, out of {n} facts">{"".join(sq)}</svg>'
            f'<p class="unit-key"><span><i class="k-found"></i> found: {tp}</span><span><i class="k-wrong"></i> wrong answer: {wrong}</span>'
            f'<span><i class="k-blank"></i> left blank: {blank}</span></p></div>')


put("units.html", '<figure class="chart" aria-labelledby="cap-u"><figcaption id="cap-u" class="chart-cap">1 square = 1 fact '
    f'in the answer key for the 25 new forms ({score("llm", "unseen")["tp"] + score("llm", "unseen")["fn"]} facts; '
    f'the {llm_u["n_cells"] - (llm_u["tp"] + llm_u["fn"])} boxes those forms left out on purpose are not counted).</figcaption><div class="unit-pair">'
    + units("llm") + units("rules") + "</div></figure>")

# --- where it slipped: 4 causes
CAUSE_TXT = {
    "priority": ("Priority read backwards on new wording", "“P2 - Normal” and “precedence 2, normal” became low; “P3 - Deferred” became high."),
    "category": ("“What broke” guessed as engine in free-text notes", "6 of these 7 answers were engine. Emails never name the category, so the AI must infer it."),
    "date": ("A report date invented when none was given", "Two copied the last-service date (a planted trap); one is a date found nowhere in the text."),
    "fallback": ("One document fell back to the plain rules", "doc_043: three answers had the date in the wrong format, so the safety net threw the whole answer away."),
}
mx = max(causes.values())
cz = ['<figure class="chart" aria-labelledby="cap-c"><figcaption id="cap-c" class="chart-cap">The '
      f'{len(E["llm"])} wrong boxes out of {llm_a["n_cells"]}, by cause. Bars start at zero.</figcaption><ol class="cause-bars">']
for k2 in ("priority", "category", "date", "fallback"):
    t1, t2 = CAUSE_TXT[k2]
    cz.append(f'<li class="cause-row"><div class="cause-head"><span class="cause-name">{esc(t1)}</span><span class="bar-val"><b>{causes[k2]}</b> boxes</span></div>'
              f'<span class="bar-track"><span class="bar-fill" style="width:{100 * causes[k2] / mx:.1f}%"></span></span>'
              f'<p class="cause-sub">{esc(t2)}</p></li>')
cz.append("</ol></figure>")
put("causes.html", "".join(cz))

# --- all 24 wrong boxes (error gallery table, filterable by cause in site.js)
OUT_WORD = {"wrong": "Wrong value", "hallucinated": "Invented", "missed": "Left blank"}
t3 = ['<div class="table-wrap" tabindex="0" role="region" aria-labelledby="cap-all"><table data-quarto-disable-processing="true" class="data" id="errtable">'
      f'<caption id="cap-all">All {len(E["llm"])} wrong boxes from the AI, from <code>exports/extraction_errors.csv</code>. '
      'All documents are SYNTHETIC.</caption><thead><tr><th scope="col">Document</th><th scope="col">Form</th>'
      '<th scope="col">Box</th><th scope="col">Answer key</th><th scope="col">AI said</th><th scope="col">What went wrong</th></tr></thead><tbody>']
for r in E["llm"]:
    f = r["field"]
    kv = show(f, truth(f, r["expected"])) or "(not stated)"
    av = show(f, pred(f, r["extracted"])) or "(blank)"
    t3.append(f'<tr data-cause="{cause(r)}"><th scope="row"><a href="read.html#{r["doc_id"]}">{r["doc_id"]}</a></th>'
              f'<td>{"new" if r["style"] == "unseen" else "familiar"} {r["layout"].replace("narrative", "email/note")}</td>'
              f'<td>{PLAIN[f]}</td><td>{esc(kv)}</td><td>{esc(av)}</td><td>✗ {OUT_WORD[r["outcome"]]}</td></tr>')
t3.append("</tbody></table></div>")
put("all_errors.html", "".join(t3))

# --- NHTSA public data: the 4 that did not match
t4 = ['<div class="table-wrap" tabindex="0" role="region" aria-labelledby="cap-nh"><table data-quarto-disable-processing="true" class="data">'
      f'<caption id="cap-nh">PUBLIC DATA. The {len(nhtsa) - nh_match} of {len(nhtsa)} NHTSA recalls where the AI’s category '
      'did not match NHTSA’s own label (from <code>exports/nhtsa_recalls.csv</code>).</caption><thead><tr>'
      '<th scope="col">Campaign</th><th scope="col">Vehicle</th><th scope="col">NHTSA says the broken part is</th>'
      '<th scope="col">AI said</th></tr></thead><tbody>']
for r in nhtsa:
    if r["match"] != "True":
        t4.append(f'<tr><th scope="row">{esc(r["campaign"])}</th><td>{esc(r["model_year"])} {esc(r["make"].title())} {esc(r["model"].upper())}</td>'
                  f'<td>{esc(r["component_nhtsa"].title())} <span class="muted">({esc(r["category_from_nhtsa"])})</span></td>'
                  f'<td>✗ {esc(r["category_llm"])}</td></tr>')
t4.append("</tbody></table></div>")
put("nhtsa.html", "".join(t4))

# --- the localhost guard, quoted from the real source file
llm_src = (ROOT / "extractor" / "llm.py").read_text(encoding="utf-8").splitlines()
guard = [ln for ln in llm_src if "LOCAL_HOSTS" in ln or "NonLocalEndpointError(" in ln or "host = urlparse" in ln]
test_src = (ROOT / "tests" / "test_extract.py").read_text(encoding="utf-8")
m_urls = re.search(r'parametrize\("url", \[(.*?)\]\)\s*\ndef (test_client_refuses_non_local_endpoints)', test_src, re.S)
if not (len(guard) >= 3 and m_urls):
    sys.exit("build_data: the localhost guard or its test moved; update the privacy page")
urls = re.findall(r'"([^"]+)"', m_urls.group(1))
put("guard.html", '<pre class="code" aria-label="Lines quoted from extractor/llm.py"><code>'
    + esc("\n".join(ln.rstrip() for ln in guard)) + "</code></pre>"
    + f'<p class="code-note">Quoted at build time from <code>extractor/llm.py</code>. The test '
      f'<code>{m_urls.group(2)}</code> tries {len(urls)} outside addresses and passes only if every one is refused: '
      + ", ".join(f"<code>{esc(u)}</code>" for u in urls) + ".</p>")

# --- status line on every page
put("status.html", f'<p class="status-badge"><span class="sb-item"><b>Run</b> {results["date"]}</span>'
    f'<span class="sb-item"><b>AI model</b> {results["model"]}, on this PC</span>'
    f'<span class="sb-item"><b>Documents</b> {results["n_docs"]} SYNTHETIC</span>'
    f'<span class="sb-item"><b>Tests</b> {esc(pytest_s)}</span></p>')

# ================================================================== error gallery example cards (quoted from the real text)
EXAMPLES = {"priority": ["doc_046", "doc_031", "doc_029"], "category": ["doc_011", "doc_035", "doc_039"],
            "date": ["doc_002", "doc_023", "doc_014"], "fallback": ["doc_043"]}
FIELD_OF = {"priority": "priority", "category": "failure_category", "date": "date_reported", "fallback": "date_reported"}


def snippet(doc: dict, f: str) -> str:
    """The line(s) of the document holding field f (or, for an invented date, the decoy date the AI copied)."""
    text = doc["text"]
    sp = next((x for x in doc["spans"] if x["f"] == f and x["k"] == "field"), None) or \
        next((x for x in doc["spans"] if x["f"] == f and x["k"] == "trap"), None)
    if not sp:
        return ""
    a = text.rfind("\n", 0, sp["s"]) + 1
    if doc["layout"] == "table" and a > 0:  # include the label line above the value
        a = text.rfind("\n", 0, a - 1) + 1
    b = text.find("\n", sp["e"])
    b = len(text) if b < 0 else b
    cls = "ex-mark" if sp["k"] == "field" else "ex-mark is-trap"
    return (esc(text[a:sp["s"]]) + f'<mark class="{cls}">' + esc(text[sp["s"]:sp["e"]]) + "</mark>" + esc(text[sp["e"]:b]))


for c5, ids in EXAMPLES.items():
    f = FIELD_OF[c5]
    cards = ['<div class="ex-grid">']
    for d in ids:
        doc = DOCS[d]
        row = next(r for r in doc["rows"] if r["f"] == f)
        assert cause(err_idx[("llm", d, f)]) == c5, (d, c5)
        kind = f'{"new" if doc["style"] == "unseen" else "familiar"} {doc["layout"].replace("narrative", "email/note")}'
        extra = ""
        if c5 == "fallback" and attempts_043:
            extra = ('<p class="ex-note">The AI’s three answers for the date: ' + ", then ".join(
                f"<code>{esc(a)}</code>" for a in sorted(attempts_043, key=lambda x: x != "19-JUL-2025"))
                + '. The checker wants <code>2025-07-19</code>, so all three were rejected.</p>')
        note = {"hallucinated": "Invented: the document gives no report date", "wrong": "Wrong value", "missed": "Left blank"}[row["aio"]]
        cards.append(f'<article class="ex-card"><header><a href="read.html#{d}"><b>{d}</b></a> <span class="muted">· {kind} · SYNTHETIC</span></header>'
                     f'<pre class="ex-text">{snippet(doc, f)}</pre>'
                     f'<dl class="ex-dl"><div><dt>Answer key</dt><dd>{esc(row["key"] or "(not stated)")}</dd></div>'
                     f'<div class="is-bad"><dt>AI said</dt><dd>✗ {esc(row["ai"] or "(blank)")}</dd></div></dl>'
                     f'<p class="ex-verdict">{note}{". Dotted: the last-service date, a planted decoy" if row["aio"] == "hallucinated" and "is-trap" in snippet(doc, f) else ""}.</p>{extra}</article>')
    cards.append("</div>")
    put(f"ex_{c5}.html", "".join(cards))

# ================================================================== glossary (site/glossary.json is the hand-written source)
GLOSS = json.loads((SITE / "glossary.json").read_text(encoding="utf-8"))
gtext = json.dumps(GLOSS, ensure_ascii=False)
for must in (f"gave {llm_u['tp'] + llm_u['fp']} answers and {llm_u['tp']} were right: {pct(llm_u['p']):.1f}%",
             f"there were {llm_u['tp'] + llm_u['fn']} facts; the AI found {llm_u['tp']} ({pct(llm_u['r']):.1f}%) and the plain rules {rul_u['tp']} ({pct(rul_u['r']):.1f}%)",
             f"{inv_dates} invented dates in {llm_a['n_cells']} boxes; {inv_num} invented or wrong numbers in the {num_cells} hours",
             f"The AI matched {nh_match}", f"{len(nhtsa)} recalls", f"{len(fda)} recall reasons", f"{results['n_docs']} work orders"):
    if must.replace("\'", "'") not in gtext:
        sys.exit(f"build_data: glossary.json no longer matches the run (expected text: {must!r})")
g = ['<dl class="glossary">']
for t in GLOSS:
    g.append(f'<div class="gl-entry" id="{t["id"]}"><dt>{esc(t["term"])}</dt><dd><p>{esc(t["def"])}</p>'
             f'<p class="gl-here"><b>In this project:</b> {esc(t["here"])}</p></dd></div>')
g.append("</dl>")
put("glossary.html", "".join(g))

# ================================================================== the reader's data + PDFs
(SITE / "data").mkdir(exist_ok=True)
payload = {"glossary": {t["id"]: {"term": t["term"], "def": t["def"]} for t in GLOSS}, "plain": PLAIN, "fields": FIELDS, "featured": [d for d, _ in FEATURED], "docs": docs_out,
           "attempts043": attempts_043}
(SITE / "data" / "site-data.js").write_text(
    "/* GENERATED by site/build_data.py from exports/ + data/docs (SYNTHETIC). */\nwindow.LLE = "
    + json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + ";\n", encoding="utf-8")
if HAVE_DOCS:
    (SITE / "pdf").mkdir(exist_ok=True)
    for d in key:
        shutil.copyfile(ROOT / "data" / "docs" / f"{d}.pdf", SITE / "pdf" / f"{d}.pdf")

# brand files (the dev-team brand folder is the source of truth when it is next to this repo)
BRAND = ROOT.parent / "dev-team" / "brand"
dst = SITE / "assets" / "brand"
if BRAND.exists():
    dst.mkdir(parents=True, exist_ok=True)
    for rel in ("tokens/base.css", "tokens/themes/manila.css", "wallpaper/hl-wallpaper.js", "wallpaper/hl-wallpaper.css",
                "logo/favicon.svg", "logo/monogram-light.svg", "logo/monogram-dark.svg"):
        shutil.copyfile(BRAND / rel, dst / Path(rel).name)

print(f"build_data: OK. {len(docs_out)} SYNTHETIC documents, {len(E['llm'])} wrong AI boxes of {llm_a['n_cells']}, "
      f"new-form AI P/R {pct(llm_u['p'])}/{pct(llm_u['r'])}, rules R {pct(rul_u['r'])}, NHTSA {nh_match}/{len(nhtsa)}; "
      f"{n_unloc} answer-key values had no highlight. All checks against exports/ passed.")
if os.environ.get("LLE_STRICT_SPANS") and n_unloc:
    sys.exit(1)
