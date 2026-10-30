"""NHTSA's public recalls API (no key needed). Responses are cached on disk.

Each recall has a free-text Summary and NHTSA's own Component label, e.g. "SERVICE BRAKES, HYDRAULIC". That label
is a real-world answer key: the LLM reads only the free text, and its category is compared with NHTSA's.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

API = "https://api.nhtsa.gov/recalls/recallsByVehicle"
VEHICLES = [("ford", "f-150", 2020), ("chevrolet", "silverado 1500", 2020), ("jeep", "wrangler", 2020)]
CATEGORIES = ("airbags_seatbelts", "brakes", "electrical", "powertrain", "fuel", "steering_suspension",
              "tires_wheels", "visibility_lighting", "structure_body", "other")

# NHTSA component prefix -> our category. First matching prefix wins.
COMPONENT_MAP = [
    ("AIR BAGS", "airbags_seatbelts"), ("SEAT BELTS", "airbags_seatbelts"), ("SEATS", "airbags_seatbelts"),
    ("CHILD SEAT", "airbags_seatbelts"), ("SERVICE BRAKES", "brakes"), ("PARKING BRAKE", "brakes"),
    ("ELECTRICAL", "electrical"), ("ENGINE", "powertrain"), ("POWER TRAIN", "powertrain"),
    ("FUEL", "fuel"), ("STEERING", "steering_suspension"), ("SUSPENSION", "steering_suspension"),
    ("TIRES", "tires_wheels"), ("WHEELS", "tires_wheels"), ("EXTERIOR LIGHTING", "visibility_lighting"),
    ("VISIBILITY", "visibility_lighting"), ("BACK OVER PREVENTION", "visibility_lighting"),
    ("LATCHES", "structure_body"), ("STRUCTURE", "structure_body"), ("EQUIPMENT", "other"),
]


def component_categories(component: str) -> set[str]:
    """NHTSA lists one or more components separated by commas or colons; map each to our categories."""
    parts = [p.strip() for p in component.replace(":", ",").split(",") if p.strip()]
    cats = {next((c for pre, c in COMPONENT_MAP if p.upper().startswith(pre)), None) for p in parts}
    cats.discard(None)
    return cats or {"other"}


def fetch_recalls(cache_dir: Path, vehicles=VEHICLES, session: requests.Session | None = None) -> list[dict]:
    s = session or requests.Session()
    rows = []
    for make, model, year in vehicles:
        path = cache_dir / f"nhtsa_{make}_{model.replace(' ', '_')}_{year}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            r = s.get(API, params={"make": make, "model": model, "modelYear": year}, timeout=30)
            r.raise_for_status()
            data = r.json()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        for rec in data.get("results", []):
            rows.append({"campaign": rec["NHTSACampaignNumber"], "make": make, "model": model, "model_year": year,
                         "component_nhtsa": rec.get("Component", ""), "summary": rec.get("Summary", ""),
                         "consequence": rec.get("Consequence", "")})
    return rows
