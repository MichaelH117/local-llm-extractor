# Local LLM Extractor

**Messy PDFs, a scraped government web page and a public API turned into clean, validated tables by a language
model that runs on this laptop. Nothing is sent to a cloud AI service. Every number below is scored against an
answer key.**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-2EC4D6?labelColor=0B0D0C)](#how-to-run-it)
[![Ollama + Gemma 3](https://img.shields.io/badge/Ollama-gemma3%3A4b-F2A93B?labelColor=0B0D0C)](#why-a-local-llm)
[![MIT license](https://img.shields.io/badge/license-MIT-8ACE00?labelColor=0B0D0C)](LICENSE)

> **The documents are synthetic.** A seeded generator invents every work order, serial number and cost. No real
> units, equipment, people or contracts. The web and API data are public US government records (FDA, NHTSA).

## Results at a glance

<!-- results:start -->
| Result | Value | What is behind it |
|---|---|---|
| Documents (SYNTHETIC) | **50 PDFs** | invented by a seeded generator; 3 layouts (form, table, free-text email); 25 seen + 25 unseen templates; answer key never read by the extractors |
| LLM field precision / recall, all docs | **95.7% / 95.2%** | gemma3:4b on a laptop CPU, 9 fields x 50 docs |
| Rules field precision / recall, all docs | **99.7% / 70.4%** | regex + keyword baseline written while looking at the seen docs |
| Unseen templates: LLM vs rules recall | **91.9% vs 41.0%** | new labels, synonyms and formats neither was tuned on |
| Seen templates: LLM vs rules recall | **98.6% vs 100.0%** | the rules' home ground |
| Hybrid (LLM, nulls filled by rules) recall | **95.2%** | precision 95.7% |
| Docs with all 9 fields right (LLM) | **68.0%** | rules 50.0%, hybrid 68.0% |
| Invalid LLM answers | **1 retried, 1 fell back to rules** | pydantic rejected the JSON; the error was sent back to the model |
| LLM time per real model call | **15.2 s average** | 59 uncached calls in 899 s; CPU only (Intel Iris Xe, no CUDA GPU), temperature 0; cached answers take ~0 s |
| NHTSA recalls (public API): LLM category = NHTSA component | **31 of 35** | public data: real free-text summaries; NHTSA's own component label is the answer key |
| FDA recalls scraped and classified | **10 rows** | public data; robots.txt checked, 1 page, cached; no answer key (hand spot-check below) |
| pytest | **32 passed, 0 failed** | recorded 2026-10-30 |

Numbers from `python -m pipeline.run --seed 42 --test` (939 s run, 59 real model calls), stored in [docs/results.json](docs/results.json).
<!-- results:end -->

## Why a local LLM?

A lot of useful data is trapped in PDFs, emails and free-text notes: maintenance work orders, warranty claims,
inspection reports. Large language models are good at reading that kind of text and filling in a form. The usual
way to use one is to send the text to a cloud API.

For defense, government and other regulated work that is often not allowed. Controlled Unclassified Information
(CUI), ITAR technical data, health records and contract data generally cannot be pasted into a public cloud service.
A model that runs **on the analyst's own machine** keeps the data where it already is:

- The model (`gemma3:4b`, about 3.3 GB) runs in [Ollama](https://ollama.com) on `localhost`.
- [extractor/llm.py](extractor/llm.py) refuses to send anything to a host that is not `localhost`/`127.0.0.1`/`::1`,
  and a test checks that.
- It runs on a laptop CPU (Intel Iris Xe graphics, no NVIDIA GPU), so it also works on an air-gapped machine once
  the model file is copied over.

The trade-off is speed and size: a 4-billion-parameter model on a CPU took about 15 seconds per call in the
recorded run and makes mistakes a bigger model would not. The results below measure exactly how many.

## What this project does

```
generator ──> 50 PDFs ──> pypdf text ──┬──> local Gemma (JSON schema) ──> pydantic ──┬──> DuckDB ──> Power BI CSVs
(seeded)      + answer key             │         ^  invalid? send the error back,     │
                                       │         └─ retry up to 3x, then rules ───────┤
                                       └──> regex/keyword rules (baseline) ───────────┘──> score vs answer key

fda.gov recalls page ──> polite scraper (robots.txt, 3 s delay, cache) ──> local Gemma: reason category ──┐
api.nhtsa.gov recalls ──> JSON API (no key, cached) ──> local Gemma: defective system ──> vs NHTSA's label ┴─> DuckDB
```

1. **Makes 50 fake maintenance work orders as PDFs** ([generator/make_docs.py](generator/make_docs.py)). Each has 9
   fields: work order number, date reported, equipment type, serial number, failure category, labour hours, parts
   cost, priority and warranty claim. Three layouts: a labelled form, a terse table and a free-text email from a
   mechanic. They are messy on purpose: label typos ("Seriel #", "Prioirty"), five date formats, money written as
   `$1,234.50`, `1234.50 USD` or `USD 1,234.50`, spelling mistakes, and in the emails a second date (last service)
   and an hour-meter reading that look like answers but are not. In the emails the failure category is never
   named; the model has to infer "hydraulic" from "fluid weeping from the lift cylinder seal".
2. **Splits them into two halves.** Docs 1-25 use the "seen" templates: I wrote the rule-based extractor while
   looking at these. Docs 26-50 use "unseen" templates: new labels ("Defect Area", "Precedence"), synonyms
   ("Powerplant" = engine, "P1 - Critical" = high, "Claimable: Expired" = no warranty), new formats (`2025.01.27`,
   `27-JAN-2025`, `1h30m`, `412.00 dollars`). Neither the rules nor the LLM prompt were changed after looking at
   the unseen half. This is the honest test of "what happens when next month's forms look different".
3. **Extracts every PDF two ways** ([extractor/](extractor/)):
   - **Local LLM.** The PDF text goes to Gemma through Ollama's `/api/chat` with a JSON schema in `format`, so the
     model can only produce that shape. Temperature 0 and a fixed seed make it repeatable. The answer is validated
     by a [pydantic model](extractor/schema.py) (allowed values, `WO-YYYY-NNNNN` and `SN-XXX-NNNNN` patterns,
     real dates, sane ranges). If validation fails, the error message is sent back to the model and it gets
     another try, up to 3. If all 3 fail, the rule-based extractor answers instead, so one bad document never
     stops the batch.
   - **Rules** ([extractor/rules.py](extractor/rules.py)): regular expressions and keyword lists. This is the
     baseline the LLM has to beat, and the fallback.
   - **Hybrid** (an experiment): the LLM's answer with any blank field filled from the rules.
4. **Scores each method against the hidden answer key**, field by field ([pipeline/evaluate.py](pipeline/evaluate.py)).
   Precision = when it gives an answer, how often it is right. Recall = how many of the real values it found.
   A wrong value counts against both.
5. **Scrapes one public web page politely** ([sources/scraper.py](sources/scraper.py)): the FDA's recalls listing
   (US government work, public domain). It reads `robots.txt` first and stops if the path is disallowed, sends an
   honest User-Agent, waits at least 3 seconds between requests (longer if the site asks), makes one page request,
   and caches the HTML so re-runs do not hit the site. The local model sorts each recall reason into a category
   (allergen, microbial, chemical...) and pulls out the named hazard ("Listeria monocytogenes", "sesame").
6. **Calls one public JSON API** ([sources/nhtsa.py](sources/nhtsa.py)): NHTSA's vehicle recalls API (no key) for
   three 2020 trucks/SUVs. The model reads only the free-text summary and names the defective system. NHTSA's own
   `Component` field is a real-world answer key for that.
7. **Loads everything into DuckDB and writes Power BI-ready CSVs** ([exports/](exports/)), described in the
   [data dictionary](docs/data_dictionary.md).

## Where the LLM fell short

<!-- failures:start -->
All numbers in this section come from [exports/extraction_errors.csv](exports/extraction_errors.csv) and
[exports/field_accuracy.csv](exports/field_accuracy.csv) of the recorded run. All 50 work orders are **SYNTHETIC**.

### Seen vs unseen templates, per field

P = precision, R = recall. "n/a" means the method gave no answer at all for that field.

| Field | Seen: LLM P / R | Seen: rules P / R | Unseen: LLM P / R | Unseen: rules P / R |
|---|---|---|---|---|
| `work_order_id` | 100% / 100% | 100% / 100% | 100% / 100% | 100% / 100% |
| `date_reported` | 88% / 100% | 100% / 100% | 100% / 96% | n/a / 0% |
| `equipment_type` | 100% / 100% | 100% / 100% | 100% / 100% | 100% / 100% |
| `serial_number` | 100% / 100% | 100% / 100% | 100% / 100% | 100% / 100% |
| `failure_category` | 88% / 88% | 100% / 100% | 84% / 84% | 100% / 24% |
| `labor_hours` | 100% / 100% | 100% / 100% | 100% / 96% | n/a / 0% |
| `parts_cost_usd` | 100% / 100% | 100% / 100% | 100% / 96% | n/a / 0% |
| `priority` | 100% / 100% | 100% / 100% | 62% / 60% | 83% / 20% |
| `warranty_claim` | 100% / 100% | 100% / 100% | 100% / 96% | 100% / 20% |
| **All 9 fields** | **97.3% / 98.6%** | **100% / 100%** | **94.0% / 91.9%** | **98.9% / 41.0%** |

On the seen templates the rules win: they were written against them. On the unseen templates the rules almost
never guess wrong, but they find only 41% of the values; the LLM finds 92%. The hybrid (LLM, blanks filled by
rules) scored exactly the same as the LLM, because the model never left a field blank that the rules could fill.

### The 24 wrong LLM cells, grouped by cause

The LLM got 24 of 450 cells wrong: 16 wrong values, 3 values invented where the answer was "not stated", and
5 blanks. Counted by root cause:

| Cause | Cells | Where | Real examples |
|---|---|---|---|
| **Priority scale read backwards on the new "P1/P2/P3" and "precedence" wording** | 9 | unseen only (table, form, email) | doc_031 `P2 - Normal` -> low (should be medium); doc_029 "precedence 2, normal" -> low; doc_046 `P3 - Deferred` -> **high** (should be low) |
| **Failure category: defaults to "engine" in free-text emails** | 7 | 6 email, 1 form; 3 seen, 4 unseen | doc_011 "alternator not charging" -> engine (electrical); doc_035 "drum brake shoes glazed" -> engine (tires_brakes); doc_039 "Heat mgmt / thermostat stuck closed" -> electrical (cooling) |
| **Date invented when no report date is given** | 3 | seen emails only | doc_002 "came in earlier this week ... last PM was 01-25-25" -> 2025-01-25 (copied the last-service date, the planted trap); doc_023 same trap, 21 Nov 2024; doc_014 -> 2024-12-11, a date that appears nowhere in the text (last PM was 2024-12-09) |
| **Whole document fell back to rules (doc_043)** | 5 | unseen table | date, labour hours, parts cost, priority and warranty all blank, because the rules do not know the unseen labels |

Priority is the weakest field: of the 9 unseen documents whose answer was **medium**, the LLM got 1 right. It
mapped "P2 - Normal", "precedence 2, normal" and "regular queue" to low. "P1 - Critical" (4 of 4) and
"drop everything" were fine. Failure-category accuracy by layout: table 100%, form 94%, free-text email 65%
(11 of 17).

**Hallucinated numbers vs missed fields.** The model did not invent a single number. Labour hours and parts cost
had 0 false positives, and in all 5 cells where the document left one of them out on purpose it correctly
answered blank. Its only invented values are the 3 dates above. All 5 missed fields come from doc_043, which
the LLM did not answer at all.

**Why doc_043 fell back.** Its date is written `19-JUL-2025`. The model copied it as `19-JUL-2025`, and after
being shown the pydantic error twice it answered `19-07-2025` both times, still not `YYYY-MM-DD`. After 3
invalid answers the pipeline used the rules. The other 8 fields in the model's answers were 7 right and 1 wrong
(priority low instead of medium), so throwing away the whole record cost 3 correct values (labour hours, parts
cost, warranty). Fix to try next:
normalise common date formats in the validator, or keep the valid fields and blank only the bad one.

### Spot-check of the public-data classifications

**FDA recalls (no answer key, so checked by hand).** The scraped page had 10 recalls; I read all 10 reason texts
against the model's category and named hazard. **10 of 10 categories agree** (lead -> chemical; soy/tree nuts and
sesame -> allergen; *C. botulinum*, *Listeria* x2 and Shiga-toxin *E. coli* -> microbial; sildenafil/tadalafil/
dapoxetine -> undeclared drug; glass -> foreign material; "potential potency issues" -> quality_or_potency). The
named hazard was also right in all 10 (left blank for the potency recall, which names none). Honest caveat: these
10 reasons are short and each names its hazard outright, and none fell in the `labeling` or `other` classes, so
this says little about harder cases. 10 rows is a smoke test, not a measurement.

**NHTSA recalls: the 4 of 35 that did not match NHTSA's component label.**

| Campaign | NHTSA component | Model said | What happened |
|---|---|---|---|
| 24V852000 | Equipment: other: labels | visibility_lighting | Missing air-bag warning label; a label belongs in `other` |
| 21V985000 | Tires: spare tire | brakes | Wrong spare tire makes ABS misbehave; model picked the consequence |
| 23V191000 | Structure: frame and members | fuel | Frame stud can puncture the fuel tank; it even named the part "frame assembly stud" |
| 25V741000 | Electrical: traction battery | fuel | High-voltage battery fire; nothing to do with fuel |

Three of the four name the system that gets hurt, not the system that is broken, even though the prompt says
"pick the system where the defect is". The prompt's own example ("a fuel leak that may cause a fire is fuel")
probably pulls fire-related recalls toward `fuel`.
<!-- failures:end -->

## How to run it

You need [Ollama](https://ollama.com/download) and the model (about 3.3 GB, one time):

```powershell
ollama pull gemma3:4b
```

Then, Windows (PowerShell), Python 3.12 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pipeline.run --test     # generate, extract, score, scrape, export, pytest
```

macOS / Linux: the same with `.venv/bin/python`, or `make setup test`.

A cold run calls the model about 100 times (50 documents plus retries, and 45 recall texts); at the recorded
~15 s per call that is roughly 25 minutes on a laptop CPU (much less with a GPU).
Every model answer is cached in `data/llm_cache/`, so later runs finish in seconds and give identical numbers.
`--no-cache` calls the model again; `--offline` skips the two web requests and uses whatever is cached;
`--model` tries a different Ollama model.

Tests only: `.venv\Scripts\python -m pytest`. The unit tests use a fake LLM and a fake HTTP session and run in a
few seconds. One test is marked `integration` and calls the real local model; it is skipped if Ollama is not
running (`pytest -m "not integration"` leaves it out).

## Repository map

| Path | What is in it |
|---|---|
| [generator/make_docs.py](generator/make_docs.py) | Seeded PDF generator, 3 layouts x 2 template styles, answer key |
| [extractor/schema.py](extractor/schema.py) | The pydantic record, and the JSON schema given to the model |
| [extractor/llm.py](extractor/llm.py) | Ollama client: localhost only, temperature 0, on-disk cache |
| [extractor/extract.py](extractor/extract.py) | PDF text, prompt, validate, retry, rules fallback |
| [extractor/rules.py](extractor/rules.py) | Regex + keyword baseline |
| [extractor/classify.py](extractor/classify.py) | Free text to one category, for the FDA and NHTSA records |
| [sources/](sources/) | Polite FDA scraper and NHTSA API client |
| [pipeline/run.py](pipeline/run.py) | The one command; also refreshes the results table above |
| [pipeline/evaluate.py](pipeline/evaluate.py) | Field-level precision / recall against the answer key |
| [tests/](tests/) | pytest: generator, retry/fallback with a fake LLM, scoring maths, scraper politeness, outputs, one real-model test |
| [exports/](exports/) | Power BI-ready CSVs from the last run |
| [docs/](docs/) | [Data dictionary](docs/data_dictionary.md) and [results.json](docs/results.json) |

## Limits, and what I would do with real documents

This is a portfolio project. The documents are synthetic and I have not run it on real military or contract data.

- **50 documents is a small test.** One error moves a field's score by 2 to 4 points. A real evaluation needs a
  few hundred hand-labelled real documents, with a second person checking the labels.
- **Synthetic messiness is not real messiness.** Real scans need OCR (Tesseract or similar) before any of this,
  have handwriting, stamps, multi-page tables and coffee stains. pypdf only reads PDFs that already contain text.
- **The rules look better than they would in practice.** They score near 100% on the seen half because I wrote
  them against those exact templates. The unseen half is the realistic picture of a rule set meeting a new form.
- **A 4B model on a CPU is the floor, not the ceiling.** A larger local model (Gemma 3 12B/27B, Llama 3.1 8B) on
  a GPU workstation would likely do better and faster. `--model` makes that a one-flag comparison.
- **Low-confidence answers should go to a person.** In production I would route every retried, fallback or
  "hybrid-filled" field to a human review queue instead of straight into a report, and track the correction rate
  over time.
- **Accreditation still applies.** Running locally keeps data off third-party servers, but on a government system
  the model files, Ollama and Python packages would still need to be approved and scanned under that system's
  authorisation (for example an RMF/ATO process) before use.

## License

MIT. See [LICENSE](LICENSE).
