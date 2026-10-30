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

The trade-off is speed and size: a 4-billion-parameter model on a CPU takes about half a minute per document and
makes mistakes a bigger model would not. The results below measure exactly how many.

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

The first run calls the model about 150 times and takes about 40 minutes on a laptop CPU (much less with a GPU).
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
