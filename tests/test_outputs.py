"""The committed Power BI CSVs from the last pipeline run: shape, keys and types a report can rely on."""

from pathlib import Path

import pandas as pd
import pytest

EXPORTS = Path(__file__).resolve().parents[1] / "exports"
pytestmark = pytest.mark.skipif(not (EXPORTS / "work_orders.csv").exists(), reason="run python -m pipeline.run first")


def test_work_orders_one_row_per_doc_with_clean_types():
    df = pd.read_csv(EXPORTS / "work_orders.csv")
    assert len(df) == 50 and df.doc_id.is_unique
    assert set(df.extraction_method) <= {"llm", "llm_retry", "rules_fallback"}
    pd.to_datetime(df.date_reported.dropna(), format="%Y-%m-%d")      # ISO dates only
    assert pd.api.types.is_numeric_dtype(df.parts_cost_usd)
    assert not df.columns.str.contains("Unnamed").any()               # no pandas index column


def test_field_accuracy_covers_every_method_and_field():
    df = pd.read_csv(EXPORTS / "field_accuracy.csv")
    assert set(df.method) == {"llm", "rules", "hybrid"}
    assert df[["precision", "recall"]].stack().between(0, 1).all()
    assert (df.groupby(["method", "style"]).field.nunique() == 10).all()   # 9 fields + ALL FIELDS


def test_errors_file_joins_back_to_work_orders():
    err = pd.read_csv(EXPORTS / "extraction_errors.csv")
    wo = pd.read_csv(EXPORTS / "work_orders.csv")
    assert set(err.doc_id) <= set(wo.doc_id)
    assert set(err.outcome) <= {"missed", "wrong", "hallucinated"}
