"""Seeded generator: fake equipment maintenance work orders as messy PDFs, plus a hidden answer key.

Three layouts:
  form      labelled "Key: value" lines, label wording and date formats vary, the odd label typo
  table     a terse two-column grid ("WO#", "S/N", "Hrs", "Parts $")
  narrative a free-text email from a mechanic; failure category, priority and warranty must be inferred,
            some numbers are missing, and a second "distractor" date and hour-meter reading are mentioned

Two template styles, so the comparison with hand-written rules is fair:
  seen      docs 1-25. The rule-based extractor was written while looking at these.
  unseen    docs 26-50. New label words, synonyms ("Powerplant", "P1", "Claimable"), new date, money and hour
            formats ("3h30m"). Neither the rules nor the LLM prompt were tuned on these.

Everything is invented. No real units, people, equipment or contracts. The answer key (answer_key.csv) is
written next to the PDFs and is only read by the scorer, never by the extractor.

Run: python -m generator.make_docs [--seed 42] [--n 50] [--out data/docs]
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FIELDS = ("work_order_id", "date_reported", "equipment_type", "serial_number", "failure_category",
          "labor_hours", "parts_cost_usd", "priority", "warranty_claim")
EQUIPMENT = ("generator", "forklift", "utility truck", "hvac unit", "air compressor", "water pump")
CATEGORIES = ("electrical", "hydraulic", "engine", "cooling", "tires_brakes")
PRIORITIES = ("low", "medium", "high")
LAYOUTS = ("form", "table", "narrative")
LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"

STYLES = {
    "seen": {
        # Symptoms never name the category outright, so the narrative layout needs real inference.
        "symptoms": {
            "electrical": ["won't crank, battery reads 9.1V and the terminal is crusted over",
                           "alternator not charging, dash lights flicker at idle",
                           "wiring harness chafed behind the panel, intermittent short to ground",
                           "starter relay clicks but nothing happens"],
            "hydraulic": ["fluid weeping from the lift cylinder seal",
                          "pump whines and the lift drifts down under load",
                          "burst hose on the boom, sprayed oil everywhere",
                          "control valve sticking, lift is jerky"],
            "engine": ["rough idle and black smoke under load", "low compression on cyl 3, likely a burnt valve",
                       "knocking noise above 1800 rpm", "won't stay running, fuel injector suspected"],
            "cooling": ["overheats after about 20 minutes of run time",
                        "radiator fins packed with mud, temp gauge pegged",
                        "coolant puddle under the front, hose clamp cracked", "fan belt snapped, temp climbing fast"],
            "tires_brakes": ["brake pads worn to the backing plate", "flat on the rear left, sidewall cut",
                             "parking brake will not hold on a grade",
                             "brake pedal goes soft, possible caliper leak"],
        },
        "priority": {"high": ["URGENT", "need this ASAP", "mission critical, down hard"],
                     "medium": ["normal priority", "get to it this week if you can", "standard turnaround"],
                     "low": ["low priority", "no rush on this one", "routine, whenever there is time"]},
        "warranty": {True: ["this one is still under OEM warranty, please file a claim",
                            "covered under warranty", "within the warranty window, claim it"],
                     False: ["out of warranty, bill the program", "not covered, warranty expired last year",
                             "customer pay, no warranty"]},
        "labels": {
            "work_order_id": ["Work Order #", "Work Ordr No.", "WO Number", "Ticket"],
            "date_reported": ["Date Reported", "Reported On", "Date", "Dt. Reportd"],
            "equipment_type": ["Equipment", "Equip. Type", "Asset Type", "Equipmnt"],
            "serial_number": ["Serial No.", "Seriel #", "S/N", "Serial Number"],
            "failure_category": ["Failure Category", "Fault Type", "Failure Cat.", "Problem Area"],
            "labor_hours": ["Labor Hours", "Labour hrs", "Hours Worked", "Lbr Hrs"],
            "parts_cost_usd": ["Parts Cost", "Parts $", "Cost of Parts", "Parts Ttl"],
            "priority": ["Priority", "Prioirty", "Urgency", "Pri."],
            "warranty_claim": ["Warranty Claim", "Warranty?", "Under Warranty", "Wrnty Claim"],
        },
        "category_names": {c: [c.replace("_", " / ").title()] for c in CATEGORIES},
        "priority_values": {p: [p.upper(), p.capitalize()] for p in PRIORITIES},
        "warranty_values": {True: ["Yes", "Y", "TRUE"], False: ["No", "N", "FALSE"]},
        "dates": [lambda d: d.isoformat(), lambda d: f"{d.month}/{d.day}/{d.year}", lambda d: d.strftime("%d %b %Y"),
                  lambda d: d.strftime("%B %d, %Y"), lambda d: f"{d.month:02d}-{d.day:02d}-{d.year % 100:02d}"],
        "money": [lambda x: f"${x:,.2f}", lambda x: f"{x:.2f} USD", lambda x: f"USD {x:,.2f}"],
        "hours": [lambda h: f"{h:g}", lambda h: f"{h:g} hrs", lambda h: f"{h:g} h"],
    },
    "unseen": {
        "symptoms": {
            "electrical": ["fuse keeps blowing on the control panel", "no power at the contactor, meter reads 0V"],
            "hydraulic": ["cylinder rod pitted and leaking oil", "pressure drops to 900 psi when lifting"],
            "engine": ["blue smoke and a misfire on cold start", "seized after losing oil pressure"],
            "cooling": ["thermostat stuck closed, running hot", "water jacket leaking, high temp alarm"],
            "tires_brakes": ["drum brake shoes glazed, long stopping distance", "tread separated on the front tire"],
        },
        "priority": {"high": ["drop everything for this one", "unit is deadlined, top precedence"],
                     "medium": ["regular queue", "precedence 2, normal"],
                     "low": ["can wait until next PM", "deferred maintenance is fine"]},
        "warranty": {True: ["claimable under the OEM plan", "vendor agreed to cover it"],
                     False: ["not claimable, plan lapsed", "vendor declined coverage"]},
        "labels": {
            "work_order_id": ["Order Ref", "Job No"],
            "date_reported": ["Opened", "Intake Date"],
            "equipment_type": ["Asset", "Unit Kind"],
            "serial_number": ["Ser. No", "Serial"],
            "failure_category": ["System", "Defect Area"],
            "labor_hours": ["Time on job", "Man-hours"],
            "parts_cost_usd": ["Material cost", "Parts total"],
            "priority": ["Severity", "Precedence"],
            "warranty_claim": ["Covered by OEM", "Claimable"],
        },
        "category_names": {"electrical": ["Elec / wiring", "Electrics"], "hydraulic": ["Hyd. system", "Fluid power"],
                           "engine": ["Powerplant", "Motor internals"], "cooling": ["Thermal / radiator", "Heat mgmt"],
                           "tires_brakes": ["Running gear (brakes)", "Wheels & stopping"]},
        "priority_values": {"high": ["P1 - Critical"], "medium": ["P2 - Normal"], "low": ["P3 - Deferred"]},
        "warranty_values": {True: ["Covered", "Claim submitted"], False: ["Not covered", "Expired"]},
        "dates": [lambda d: d.strftime("%Y.%m.%d"), lambda d: d.strftime("%d-%b-%Y").upper(),
                  lambda d: d.strftime("%b %d %Y")],
        "money": [lambda x: f"$ {x:,.2f}", lambda x: f"{x:.2f} dollars", lambda x: f"{x:,.2f} (USD)"],
        "hours": [lambda h: f"{int(h)}h{int(round(h % 1 * 60)):02d}m", lambda h: f"{h:g} man-hours"],
    },
}
TYPOS = [("the", "teh"), ("replaced", "replacd"), ("checked", "chekced"), ("receive", "recieve"),
         ("battery", "batery")]


@dataclass
class WorkOrder:
    doc_id: str
    layout: str
    style: str
    work_order_id: str
    date_reported: str | None  # ISO 8601 in the key, any format in the PDF
    equipment_type: str
    serial_number: str
    failure_category: str
    labor_hours: float | None
    parts_cost_usd: float | None
    priority: str
    warranty_claim: bool


def _typo(text: str, rng: random.Random) -> str:
    for good, bad in TYPOS:
        if good in text and rng.random() < 0.5:
            text = text.replace(good, bad, 1)
    return text


def make_order(i: int, n: int, rng: random.Random) -> WorkOrder:
    reported = date(2025, 1, 1) + timedelta(days=rng.randrange(300))
    wo = WorkOrder(
        doc_id=f"doc_{i:03d}", layout=LAYOUTS[i % 3], style="seen" if i <= n // 2 else "unseen",
        work_order_id=f"WO-{reported.year}-{rng.randrange(10000, 99999)}",
        date_reported=reported.isoformat(), equipment_type=rng.choice(EQUIPMENT),
        serial_number=f"SN-{rng.choice(LETTERS)}{rng.randrange(10)}{rng.choice(LETTERS)}-{rng.randrange(10000, 99999)}",
        failure_category=rng.choice(CATEGORIES),
        labor_hours=float(rng.choice([0.5, 1, 1.5, 2, 2.5, 3, 4, 5.5, 6, 8, 12])),
        parts_cost_usd=round(rng.uniform(15, 4800), 2) if rng.random() > 0.1 else 0.0,
        priority=rng.choice(PRIORITIES), warranty_claim=rng.random() < 0.4)
    if wo.layout == "narrative":  # free text leaves things out
        if rng.random() < 0.15:
            wo.labor_hours = None
        if rng.random() < 0.15:
            wo.parts_cost_usd = None
        if rng.random() < 0.10:
            wo.date_reported = None
    return wo


def _narrative(wo: WorkOrder, s: dict, rng: random.Random) -> list[str]:
    eq = wo.equipment_type
    d = date.fromisoformat(wo.date_reported) if wo.date_reported else None
    prior = (d or date(2025, 6, 1)) - timedelta(days=rng.randrange(40, 200))
    meter = rng.randrange(300, 9000)
    fdate, money, hours = rng.choice(s["dates"]), rng.choice(s["money"]), rng.choice(s["hours"])
    symptom = rng.choice(s["symptoms"][wo.failure_category])
    warranty = rng.choice(s["warranty"][wo.warranty_claim])
    priority = rng.choice(s["priority"][wo.priority])
    if wo.style == "seen":
        lines = [f"Subject: {wo.work_order_id} - {eq} down", "",
                 f"Hi team, writing up the {eq} (s/n {wo.serial_number})"
                 + (f" that came in on {fdate(d)}." if d else " that came in earlier this week."),
                 f"Operator says: {symptom}. Meter shows {meter:,} hrs; last PM was {fdate(prior)}."]
        work = "We checked it over and replaced the failed part."
        if wo.labor_hours is not None:
            work += f" Took about {wo.labor_hours:g} hours of labor"
            work += f", parts came to {money(wo.parts_cost_usd)}." if wo.parts_cost_usd is not None else "."
        elif wo.parts_cost_usd is not None:
            work += f" Parts came to {money(wo.parts_cost_usd)}."
        lines += [_typo(work, rng), f"{warranty.capitalize()}. {priority.capitalize()}.", "",
                  rng.choice(["- Sgt. R. Doe (fictional), motor pool", "Thanks, J. Smith (fictional)", "-- shop 2"])]
    else:
        lines = [f"Shift log, bay {rng.randrange(1, 9)}" + (f", {fdate(d)}" if d else ""), "",
                 f"Job ref {wo.work_order_id}. {eq.capitalize()} ser# {wo.serial_number.replace('-', ' ')} "
                 f"brought over by the night crew. Previous service {fdate(prior)}, hour meter at {meter}.",
                 f"Found: {symptom}. Swapped the bad component and ran it for 15 min, OK."]
        spent = []
        if wo.labor_hours is not None:
            spent.append(f"spent {hours(wo.labor_hours)} on it")
        if wo.parts_cost_usd is not None:
            spent.append(f"material {money(wo.parts_cost_usd)}")
        if spent:
            lines.append(_typo(" and ".join(spent).capitalize() + ".", rng))
        lines += [f"{warranty.capitalize()}; {priority}.", "", "logged by: A. Tech (fictional)"]
    return lines


def _values(wo: WorkOrder, s: dict, rng: random.Random) -> dict[str, str]:
    """Field -> display string for the form and table layouts (formats vary per document)."""
    return {
        "work_order_id": wo.work_order_id,
        "date_reported": rng.choice(s["dates"])(date.fromisoformat(wo.date_reported)),
        "equipment_type": wo.equipment_type.title() if rng.random() < 0.7 else wo.equipment_type.upper(),
        "serial_number": wo.serial_number if rng.random() < 0.8 else wo.serial_number.replace("-", " "),
        "failure_category": rng.choice(s["category_names"][wo.failure_category]),
        "labor_hours": rng.choice(s["hours"])(wo.labor_hours),
        "parts_cost_usd": rng.choice(s["money"])(wo.parts_cost_usd),
        "priority": rng.choice(s["priority_values"][wo.priority]),
        "warranty_claim": rng.choice(s["warranty_values"][wo.warranty_claim]),
    }


def render_pdf(wo: WorkOrder, path: Path, rng: random.Random) -> None:
    s = STYLES[wo.style]
    styles = getSampleStyleSheet()
    body, h = styles["BodyText"], styles["Heading2"]
    story = []
    if wo.layout == "narrative":
        story += [Paragraph(line or "&nbsp;", body) for line in _narrative(wo, s, rng)]
    else:
        vals = _values(wo, s, rng)
        rest = list(FIELDS[1:])
        rng.shuffle(rest)
        order = [FIELDS[0], *rest]  # WO number first, the rest in a random order
        labels = {f: rng.choice(s["labels"][f]) for f in FIELDS}
        notes = _typo(f"{rng.choice(s['symptoms'][wo.failure_category])}. checked and replaced as needed.", rng)
        if wo.layout == "form":
            story.append(Paragraph(rng.choice(["MAINTENANCE WORK ORDER", "Equipment Repair Request",
                                               "Field Service Ticket"]), h))
            story += [Paragraph(f"<b>{labels[f]}:</b> {vals[f]}", body) for f in order]
            story += [Spacer(1, 8), Paragraph(f"Notes: {notes}", body)]
        else:
            story.append(Paragraph("WO SUMMARY" if wo.style == "seen" else "REPAIR RECORD", h))
            short = {"work_order_id": "WO#", "serial_number": "S/N", "labor_hours": "Hrs",
                     "parts_cost_usd": "Parts $", "warranty_claim": "Wrnty", "priority": "Pri"}
            rows = [[short.get(f, labels[f]) if wo.style == "seen" else labels[f], vals[f]] for f in order]
            t = Table(rows, colWidths=[110, 300])
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                   ("FONTSIZE", (0, 0), (-1, -1), 9)]))
            story += [t, Spacer(1, 8), Paragraph(f"Tech remarks: {notes}", body)]
    story += [Spacer(1, 16), Paragraph("<i>SYNTHETIC DOCUMENT - generated for a portfolio project.</i>", body)]
    SimpleDocTemplate(str(path), pagesize=letter, title=wo.doc_id).build(story)


def generate(seed: int = 42, n: int = 50, out: Path = Path("data/docs")) -> list[WorkOrder]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    orders = [make_order(i, n, rng) for i in range(1, n + 1)]
    for wo in orders:
        render_pdf(wo, out / f"{wo.doc_id}.pdf", rng)
    with open(out / "answer_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(orders[0])))
        w.writeheader()
        w.writerows(asdict(o) for o in orders)
    return orders


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", type=Path, default=Path("data/docs"))
    a = ap.parse_args()
    print(f"wrote {len(generate(a.seed, a.n, a.out))} PDFs to {a.out}")


if __name__ == "__main__":
    main()
