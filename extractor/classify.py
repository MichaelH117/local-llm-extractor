"""Free text -> one category (plus a short extracted phrase), for the scraped and API records."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from .llm import OllamaClient

FDA_CATEGORIES = ("undeclared_allergen", "undeclared_drug_ingredient", "microbial_contamination",
                  "chemical_contamination", "foreign_material", "quality_or_potency", "labeling", "other")


class Classified(BaseModel):
    category: str
    agent: str | None = None  # the allergen, microbe, chemical or part named in the text, if any


def classify(text: str, categories: tuple[str, ...], instructions: str, llm: OllamaClient,
             max_attempts: int = 2) -> tuple[Classified | None, int]:
    """Returns (answer, attempts). None if the model never produced a valid category (caller records 'unknown')."""
    schema = {"type": "object", "required": ["category", "agent"],
              "properties": {"category": {"type": "string", "enum": list(categories)},
                             "agent": {"type": ["string", "null"]}}}
    messages = [{"role": "system", "content": instructions},
                {"role": "user", "content": f"Text:\n\"\"\"\n{text}\n\"\"\""}]
    for attempt in range(1, max_attempts + 1):
        raw = llm.chat(messages, schema=schema)
        try:
            out = Classified.model_validate(json.loads(raw))
            if out.category in categories:
                return out, attempt
            err = f"category must be one of {', '.join(categories)}"
        except (json.JSONDecodeError, ValidationError) as e:
            err = str(e).splitlines()[0]
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": f"Invalid: {err}. Try again."}]
    return None, max_attempts


NHTSA_INSTRUCTIONS = """You classify vehicle safety recall notices by the vehicle system that is defective.
Categories: airbags_seatbelts (air bags, seat belts, seats), brakes, electrical (battery, wiring, instrument panel,
starter), powertrain (engine, transmission, clutch, driveshaft, axle), fuel (fuel pump, fuel lines),
steering_suspension, tires_wheels, visibility_lighting (headlights, wipers, rear-view camera),
structure_body (frame, doors, latches), other (labels, manuals, anything else).
Pick the system where the defect is, not the consequence (a fuel leak that may cause a fire is fuel).
agent: the defective part in 1-4 words."""

FDA_INSTRUCTIONS = """You classify US FDA product recall reasons.
Categories: undeclared_allergen (milk, eggs, nuts, soy, wheat, sesame, fish...), undeclared_drug_ingredient
(hidden drug substances), microbial_contamination (bacteria, mold, toxins from microbes), chemical_contamination
(lead, heavy metals, chemicals), foreign_material (glass, metal, plastic pieces), quality_or_potency (sub-potent,
sterility, dissolution), labeling (wrong label or missing information, not an allergen), other.
agent: the specific allergen, microbe, chemical, drug or material named, or null."""
