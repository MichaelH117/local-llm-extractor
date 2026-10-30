"""Rule-based extractor: regexes and keyword lists. It is the fallback when the LLM fails, and the baseline the
LLM has to beat. It is good at labelled forms and weak at free text, which is the point of the comparison."""

from __future__ import annotations

import re
from datetime import date, datetime

from .schema import WorkOrder

EQUIP = ["utility truck", "air compressor", "water pump", "hvac unit", "generator", "forklift"]
CATEGORY_KEYWORDS = {
    "electrical": ["electrical", "battery", "batery", "alternator", "wiring", "starter", "relay", "short"],
    "hydraulic": ["hydraulic", "hydralic", "lift cylinder", "mast", "boom", "hose on", "control valve"],
    "engine": ["engine", "compression", "knocking", "black smoke", "injector", "rough idle"],
    "cooling": ["cooling", "overheat", "radiator", "coolant", "fan belt"],
    "tires_brakes": ["tires", "brake", "flat on", "sidewall", "caliper"],
}
PRIORITY_KEYWORDS = {"high": ["urgent", "asap", "critical", "high"],
                     "low": ["low priority", "no rush", "routine", "low"],
                     "medium": ["normal priority", "get to it this week", "standard", "medium"]}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%B %d, %Y", "%m-%d-%y")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2} [A-Z][a-z]{2} \d{4}"
                     r"|[A-Z][a-z]+ \d{1,2}, \d{4}|\d{2}-\d{2}-\d{2}\b")
MONEY_RE = re.compile(r"(?:\$|USD)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*USD")


def _label_value(text: str, labels: list[str]) -> str | None:
    """Value after a label, either 'Label: value' on one line or a table cell on the next line."""
    for lab in labels:
        m = re.search(rf"^\s*{re.escape(lab)}\s*:?\s*(.*)$", text, flags=re.I | re.M)
        if m:
            val = m.group(1).strip()
            if not val:  # table layout: value on the next line
                nxt = text[m.end():].lstrip("\n").split("\n", 1)[0].strip()
                val = nxt
            return val or None
    return None


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(" ".join(s.split()), fmt).date()
        except ValueError:
            continue
    return None


def parse_money(s: str | None) -> float | None:
    if not s:
        return None
    m = MONEY_RE.search(s)
    raw = (m.group(1) or m.group(2)) if m else re.sub(r"[^\d.]", "", s)
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _first_keyword(text: str, table: dict[str, list[str]]) -> str | None:
    low = text.lower()
    hits = [(low.find(k), name) for name, kws in table.items() for k in kws if k in low]
    return min(hits)[1] if hits else None


def extract(text: str) -> WorkOrder:
    out: dict = {}
    if m := re.search(r"WO-\d{4}-\d{5}", text):
        out["work_order_id"] = m.group(0)
    if m := re.search(r"SN[-\s][A-Z0-9]{3}[-\s]\d{5}", text, flags=re.I):
        out["serial_number"] = m.group(0)

    d = _label_value(text, ["Date Reported", "Reported On", "Dt. Reportd", "Date"])
    if d is None and (m := re.search(r"came\s+in\s+on\s+(.+?)\.\s", text)):
        d = m.group(1)
    out["date_reported"] = parse_date(d)

    low = text.lower()
    eq = next((e for e in EQUIP if e in low), None)
    out["equipment_type"] = eq

    cat_label = _label_value(text, ["Failure Category", "Fault Type", "Failure Cat.", "Problem Area"])
    out["failure_category"] = _first_keyword(cat_label or text, CATEGORY_KEYWORDS)

    if m := re.search(r"about ([\d.]+)\s+hours\s+of\s+labor", text):
        hrs = m.group(1)
    else:
        hrs = _label_value(text, ["Labor Hours", "Labour hrs", "Hours Worked", "Lbr Hrs", "Hrs"])
    if hrs and (m := re.search(r"[\d.]+", hrs)):
        out["labor_hours"] = float(m.group(0))

    cost = _label_value(text, ["Parts Cost", "Parts $", "Cost of Parts", "Parts Ttl"])
    if cost is None and (m := re.search(r"[Pp]arts\s+came\s+to\s+(.+?)\.(?:\s|$)", text, flags=re.S)):
        cost = m.group(1)
    out["parts_cost_usd"] = parse_money(cost)

    pri = _label_value(text, ["Priority", "Prioirty", "Urgency", "Pri.", "Pri"])
    out["priority"] = _first_keyword(pri or text, PRIORITY_KEYWORDS)

    w = _label_value(text, ["Warranty Claim", "Warranty?", "Under Warranty", "Wrnty Claim", "Wrnty"])
    if w:
        out["warranty_claim"] = w.strip().lower() in {"yes", "y", "true"}
    elif any(k in low for k in ("out of warranty", "not covered", "no warranty", "warranty expired")):
        out["warranty_claim"] = False
    elif "warranty" in low:
        out["warranty_claim"] = True

    clean = {}
    for k, v in out.items():  # drop anything that would not validate rather than crash the fallback
        try:
            WorkOrder(**{k: v})
            clean[k] = v
        except ValueError:
            pass
    return WorkOrder(**clean)
