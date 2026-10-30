"""The target record. Pydantic validates every LLM answer before it is allowed into the database."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Equipment = Literal["generator", "forklift", "utility truck", "hvac unit", "air compressor", "water pump"]
Category = Literal["electrical", "hydraulic", "engine", "cooling", "tires_brakes"]
Priority = Literal["low", "medium", "high"]


class WorkOrder(BaseModel):
    """One maintenance work order. Any field the document does not state is null, never guessed."""

    model_config = ConfigDict(extra="forbid")

    work_order_id: str | None = Field(None, pattern=r"^WO-\d{4}-\d{5}$")
    date_reported: date | None = Field(None, description="ISO 8601, YYYY-MM-DD")
    equipment_type: Equipment | None = None
    serial_number: str | None = Field(None, pattern=r"^SN-[A-Z0-9]{3}-\d{5}$")
    failure_category: Category | None = None
    labor_hours: float | None = Field(None, ge=0, le=200)
    parts_cost_usd: float | None = Field(None, ge=0, le=100_000)
    priority: Priority | None = None
    warranty_claim: bool | None = None

    @field_validator("equipment_type", "failure_category", "priority", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("serial_number", mode="before")
    @classmethod
    def _serial(cls, v):
        # "SN U1V 65302" and "sn-u1v-65302" are the same serial; normalise before the pattern check.
        if isinstance(v, str):
            parts = v.strip().upper().replace(" ", "-").split("-")
            return "-".join(p for p in parts if p)
        return v


FIELDS = tuple(WorkOrder.model_fields)


def ollama_schema() -> dict:
    """JSON schema handed to Ollama's `format`, so the model can only emit this shape (grammar-constrained).

    Deliberately flatter than pydantic's own schema: every key required, nullable via a type list, and no
    `pattern`/`format`. With pydantic's raw schema (optional keys, anyOf, format: date) gemma3:4b returned null for
    fields that were plainly on the page in a 3-document smoke test. Pydantic still enforces the patterns
    afterwards, and a failure there triggers the retry loop.
    """
    props = {}
    for name, spec in WorkOrder.model_json_schema()["properties"].items():
        base = next(x for x in spec["anyOf"] if x.get("type") != "null")
        prop = {"type": [base["type"], "null"]}
        if "enum" in base:
            prop["enum"] = [*base["enum"], None]
        props[name] = prop
    return {"type": "object", "properties": props, "required": list(props)}
