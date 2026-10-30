"""Score extracted records against the generator's answer key, field by field.

For each field and document:
  true positive   the extractor gave a value and it matches the key
  false positive  the extractor gave a value that is wrong, or gave a value where the key says "not stated"
  false negative  the key has a value and the extractor missed it or got it wrong
A wrong value counts as both a false positive and a false negative (the usual convention for extraction).
precision = TP / (TP + FP): when it answers, how often it is right.  recall = TP / (TP + FN): how much it finds.
"""

from __future__ import annotations

import pandas as pd

from extractor.schema import FIELDS

NUMERIC = {"labor_hours", "parts_cost_usd"}


def truth_value(field: str, raw):
    """Answer-key CSV cell -> typed value (None for blank)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or raw == "":
        return None
    if field in NUMERIC:
        return float(raw)
    if field == "warranty_claim":
        return str(raw) == "True"
    return str(raw)


def same(field: str, truth, pred) -> bool:
    if truth is None or pred is None:
        return truth is None and pred is None
    if field in NUMERIC:
        return abs(float(truth) - float(pred)) < 0.01
    return str(truth).strip().lower() == str(pred).strip().lower()


def compare(key: pd.DataFrame, preds: dict[str, dict], method: str) -> pd.DataFrame:
    """One row per (document, field) with the outcome. preds: doc_id -> record dict (JSON mode)."""
    rows = []
    for k in key.itertuples(index=False):
        pred = preds[k.doc_id]
        for f in FIELDS:
            t, p = truth_value(f, getattr(k, f)), pred.get(f)
            ok = same(f, t, p)
            if ok:
                outcome = "correct" if t is not None else "correct_null"
            elif p is None:
                outcome = "missed"
            elif t is None:
                outcome = "hallucinated"
            else:
                outcome = "wrong"
            rows.append(dict(method=method, doc_id=k.doc_id, layout=k.layout, style=k.style, field=f,
                             expected=t, extracted=p, outcome=outcome))
    return pd.DataFrame(rows)


def field_scores(cmp: pd.DataFrame) -> pd.DataFrame:
    """Precision / recall / F1 per method x style x field, plus an 'all fields' row. Style 'all' = both halves."""
    both = pd.concat([cmp, cmp.assign(style="all")])
    both = pd.concat([both, both.assign(field="ALL FIELDS")])
    g = both.groupby(["method", "style", "field"])
    out = pd.DataFrame({
        "tp": g.outcome.apply(lambda s: (s == "correct").sum()),
        "fp": g.outcome.apply(lambda s: s.isin(["wrong", "hallucinated"]).sum()),
        "fn": g.outcome.apply(lambda s: s.isin(["wrong", "missed"]).sum()),
        "n_cells": g.size(),
    }).reset_index()
    out["precision"] = (out.tp / (out.tp + out.fp)).where(out.tp + out.fp > 0)
    out["recall"] = (out.tp / (out.tp + out.fn)).where(out.tp + out.fn > 0)
    out["f1"] = 2 * out.precision * out.recall / (out.precision + out.recall)
    return out.round(4)


def doc_exact(cmp: pd.DataFrame) -> pd.DataFrame:
    """Share of documents where every one of the 9 fields is right, by method and style."""
    ok = cmp.assign(ok=cmp.outcome.isin(["correct", "correct_null"])).groupby(["method", "style", "doc_id"]).ok.all()
    per = ok.reset_index()
    allstyle = per.assign(style="all")
    return pd.concat([per, allstyle]).groupby(["method", "style"]).ok.mean().round(4).rename("docs_all_correct") \
        .reset_index()
