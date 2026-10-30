"""The LLM path with a fake model: first-try success, retry after a bad answer, and the rules fallback."""

import pytest
from pydantic import ValidationError

from extractor import rules
from extractor.extract import extract_with_llm, hybrid, pdf_text
from extractor.llm import NonLocalEndpointError, OllamaClient
from extractor.schema import WorkOrder, ollama_schema
from tests.conftest import first_doc

GOOD = {"work_order_id": "WO-2025-12345", "date_reported": "2025-03-04", "equipment_type": "forklift",
        "serial_number": "SN-A1B-12345", "failure_category": "hydraulic", "labor_hours": 3.5,
        "parts_cost_usd": 412.0, "priority": "low", "warranty_claim": True}


def test_valid_first_answer(fake_llm):
    llm = fake_llm(GOOD)
    e = extract_with_llm("doc text", llm)
    assert e.method == "llm" and e.attempts == 1 and e.errors == []
    assert e.record.labor_hours == 3.5 and str(e.record.date_reported) == "2025-03-04"


def test_invalid_json_then_bad_value_then_valid_is_retried(fake_llm):
    bad_value = {**GOOD, "priority": "extreme"}
    llm = fake_llm("{not json", bad_value, GOOD)
    e = extract_with_llm("doc text", llm, max_attempts=3)
    assert e.method == "llm_retry" and e.attempts == 3 and len(e.errors) == 2
    assert "priority" in e.errors[1]
    # The validation error is fed back to the model on the next turn.
    assert "invalid" in llm.sent[2][-1]["content"].lower() and "priority" in llm.sent[2][-1]["content"]


def test_falls_back_to_rules_after_max_attempts(fake_llm, docs):
    path, row = first_doc(docs, "form")
    text = pdf_text(path)
    llm = fake_llm("nope", "still nope", "{}{}")
    e = extract_with_llm(text, llm, max_attempts=3)
    assert e.method == "rules_fallback" and len(e.errors) == 3
    assert e.record.work_order_id == row.work_order_id


def test_hybrid_fills_only_nulls():
    llm_rec = WorkOrder(**{**GOOD, "labor_hours": None})
    rule_rec = WorkOrder(**{**GOOD, "labor_hours": 9.0, "priority": "high"})
    merged = hybrid(llm_rec, rule_rec)
    assert merged.labor_hours == 9.0 and merged.priority == "low"


def test_schema_normalises_serial_and_rejects_bad_enum():
    assert WorkOrder(serial_number="sn u1v 65302").serial_number == "SN-U1V-65302"
    with pytest.raises(ValidationError):
        WorkOrder(failure_category="plumbing")
    with pytest.raises(ValidationError):
        WorkOrder(unexpected_field=1)


def test_ollama_schema_requires_every_key_and_allows_null():
    s = ollama_schema()
    assert set(s["required"]) == set(s["properties"]) == set(WorkOrder.model_fields)
    assert all("null" in p["type"] for p in s["properties"].values())


@pytest.mark.parametrize("url", ["https://api.example.com", "http://10.0.0.5:11434", "http://my-gpu-box:11434"])
def test_client_refuses_non_local_endpoints(url):
    with pytest.raises(NonLocalEndpointError):
        OllamaClient(url)


def test_client_caches_answers(tmp_path, monkeypatch):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": '{"a": 1}'}}

    monkeypatch.setattr("extractor.llm.requests.post", lambda url, json, timeout: calls.append(json) or Resp())
    c = OllamaClient(cache_dir=tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    assert c.chat(msgs, {"type": "object"}) == c.chat(msgs, {"type": "object"}) == '{"a": 1}'
    assert len(calls) == 1 and c.calls == 1
    assert calls[0]["options"]["temperature"] == 0 and calls[0]["stream"] is False


def test_rules_parsers():
    assert rules.parse_money("USD\n2,700.56") == 2700.56
    assert rules.parse_money("$0") == 0.0
    assert str(rules.parse_date("April\n30, 2025")) == "2025-04-30"
    assert str(rules.parse_date("06-24-25")) == "2025-06-24"
    assert rules.parse_date("someday") is None
