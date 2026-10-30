import pandas as pd
import pytest

from extractor import rules
from extractor.extract import pdf_text
from extractor.schema import FIELDS
from pipeline.evaluate import compare, doc_exact, field_scores

KEY = pd.DataFrame([
    dict(doc_id="d1", layout="form", style="seen", work_order_id="WO-2025-11111", date_reported="2025-01-02",
         equipment_type="forklift", serial_number="SN-A1B-11111", failure_category="engine", labor_hours=2.0,
         parts_cost_usd=10.0, priority="low", warranty_claim=True),
    dict(doc_id="d2", layout="narrative", style="unseen", work_order_id="WO-2025-22222", date_reported=None,
         equipment_type="generator", serial_number="SN-C2D-22222", failure_category="cooling", labor_hours=None,
         parts_cost_usd=20.0, priority="high", warranty_claim=False),
])


def _pred(row, **changes):
    d = {f: row[f] for f in FIELDS}
    d = {k: (None if pd.isna(v) else v) for k, v in d.items()}
    d.update(changes)
    return d


def test_outcomes_and_scores():
    preds = {"d1": _pred(KEY.iloc[0], priority=None, parts_cost_usd=10.004),   # missed, numeric tolerance
             "d2": _pred(KEY.iloc[1], labor_hours=5.0, equipment_type="forklift")}  # hallucinated, wrong
    cmp = compare(KEY, preds, "m")
    out = dict(zip(zip(cmp.doc_id, cmp.field, strict=True), cmp.outcome, strict=True))
    assert out[("d1", "priority")] == "missed"
    assert out[("d1", "parts_cost_usd")] == "correct"
    assert out[("d2", "labor_hours")] == "hallucinated"
    assert out[("d2", "equipment_type")] == "wrong"
    assert out[("d2", "date_reported")] == "correct_null"

    s = field_scores(cmp)
    allrow = s[(s["style"] == "all") & (s.field == "ALL FIELDS")].iloc[0]
    # 18 cells: 1 correct_null, 3 errors -> tp 14; fp = wrong + hallucinated = 2; fn = wrong + missed = 2
    assert (allrow.tp, allrow.fp, allrow.fn) == (14, 2, 2)
    assert allrow.precision == pytest.approx(14 / 16, abs=1e-4)
    ex = doc_exact(cmp)
    assert ex[ex["style"] == "all"].docs_all_correct.iloc[0] == 0.0


def test_rules_are_perfect_on_the_templates_they_were_written_for(docs):
    """The rules were developed on the 'seen' half. This guards against silently breaking them."""
    k = docs["key"]
    seen = k[k["style"] == "seen"]
    preds = {d: rules.extract(pdf_text(docs["dir"] / f"{d}.pdf")).model_dump(mode="json") for d in seen.doc_id}
    s = field_scores(compare(seen, preds, "rules"))
    assert s[(s["style"] == "all") & (s.field == "ALL FIELDS")].recall.iloc[0] >= 0.98
