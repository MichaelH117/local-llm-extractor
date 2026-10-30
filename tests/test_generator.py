import pandas as pd

from extractor.extract import pdf_text
from extractor.schema import FIELDS, WorkOrder
from generator.make_docs import generate
from pipeline.evaluate import truth_value


def test_fifty_docs_three_layouts_two_styles(docs):
    k = docs["key"]
    assert len(k) == 50 and len(list(docs["dir"].glob("*.pdf"))) == 50
    assert set(k.layout) == {"form", "table", "narrative"}
    assert (k["style"] == "seen").sum() == 25 and (k["style"] == "unseen").sum() == 25


def test_seeded_and_repeatable(docs, tmp_path):
    generate(seed=42, n=50, out=tmp_path)
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "answer_key.csv"), docs["key"])


def test_answer_key_rows_are_valid_records(docs):
    for row in docs["key"].itertuples(index=False):
        WorkOrder(**{f: truth_value(f, getattr(row, f)) for f in FIELDS})


def test_every_pdf_has_text_and_its_work_order_id(docs):
    for row in docs["key"].itertuples(index=False):
        text = pdf_text(docs["dir"] / f"{row.doc_id}.pdf")
        assert row.work_order_id in text
        assert "SYNTHETIC DOCUMENT" in text


def test_only_free_text_docs_have_missing_values(docs):
    k = docs["key"]
    has_blank = k[list(FIELDS)].isna().any(axis=1)
    assert set(k[has_blank].layout) <= {"narrative"}
