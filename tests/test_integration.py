"""Hits the real local model. Skipped (not failed) when Ollama or gemma3:4b is not available."""

import pytest

from extractor.extract import extract_with_llm, pdf_text
from extractor.llm import OllamaClient
from tests.conftest import first_doc

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def llm():
    c = OllamaClient()  # no cache: this must be a real call
    if not c.available():
        pytest.skip("Ollama with gemma3:4b is not running on localhost")
    return c


def test_real_model_extracts_a_form(llm, docs):
    path, row = first_doc(docs, "form")
    e = extract_with_llm(pdf_text(path), llm)
    assert llm.calls >= 1
    assert e.method in {"llm", "llm_retry"}
    assert e.record.work_order_id == row.work_order_id
    assert e.record.serial_number == row.serial_number
