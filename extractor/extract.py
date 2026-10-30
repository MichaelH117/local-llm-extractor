"""PDF -> text -> local LLM (JSON schema) -> pydantic -> record, with retry and a rule-based fallback."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from pypdf import PdfReader

from . import rules
from .llm import OllamaClient
from .schema import FIELDS, WorkOrder, ollama_schema

SYSTEM_PROMPT = """You extract fields from equipment maintenance work orders into JSON.
Rules:
- Use only facts stated in the document. If a field is not stated or cannot be inferred with confidence, use null.
- work_order_id looks like WO-2025-12345. serial_number looks like SN-A1B-12345 (use dashes, upper case).
- date_reported: the date the equipment came in or the report date, as YYYY-MM-DD. Not the last service date.
- equipment_type: one of generator, forklift, utility truck, hvac unit, air compressor, water pump.
- failure_category: one of electrical, hydraulic, engine, cooling, tires_brakes. Infer it from the symptoms if
  there is no label (battery/wiring/alternator = electrical, fluid leak/lift/boom = hydraulic, overheating/
  radiator/coolant = cooling, smoke/compression/knocking = engine, brakes/tires = tires_brakes).
- labor_hours: hours of repair labor (a number). Not the equipment's hour meter.
- parts_cost_usd: total parts cost as a plain number, no $ sign or commas.
- priority: low, medium or high. "urgent", "ASAP", "critical" = high; "no rush", "routine" = low;
  "normal", "standard", "this week" = medium.
- warranty_claim: true if the repair is covered by / claimed under warranty, false if not covered.
Return only the JSON object."""


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()


@dataclass
class Extraction:
    record: WorkOrder
    method: str                       # "llm", "llm_retry" or "rules_fallback"
    attempts: int
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


def extract_with_llm(text: str, llm: OllamaClient, max_attempts: int = 3) -> Extraction:
    """Ask the model; if its answer is not valid JSON or fails validation, show it the error and ask again.
    After max_attempts bad answers, fall back to the rule-based extractor so the pipeline never stops."""
    t0 = time.perf_counter()
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Document:\n\"\"\"\n{text}\n\"\"\""}]
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        raw = llm.chat(messages, schema=ollama_schema())
        try:
            rec = WorkOrder.model_validate(json.loads(raw))
            return Extraction(rec, "llm" if attempt == 1 else "llm_retry", attempt, errors,
                              time.perf_counter() - t0)
        except (json.JSONDecodeError, ValidationError) as e:
            msg = str(e).splitlines()[0] if isinstance(e, json.JSONDecodeError) else _short(e)
            errors.append(msg)
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"That answer was invalid: {msg}. "
                                                     "Fix it and return only the corrected JSON object."}]
    return Extraction(rules.extract(text), "rules_fallback", max_attempts, errors, time.perf_counter() - t0)


def _short(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:3])


def hybrid(llm_rec: WorkOrder, rules_rec: WorkOrder) -> WorkOrder:
    """LLM answer, with any field it left null filled from the rules (an experiment, reported separately)."""
    merged = {f: getattr(llm_rec, f) if getattr(llm_rec, f) is not None else getattr(rules_rec, f) for f in FIELDS}
    return WorkOrder(**merged)
