# Data dictionary

Every file in [exports/](../exports/) is UTF-8 CSV with a header row, no index column, snake_case names, ISO 8601
dates (`YYYY-MM-DD`) and plain numbers (no `$` or commas), so Power BI's "Get data > Text/CSV" types them
correctly without manual fixes. The same tables are in `data/extractions.duckdb` after a run.

Suggested Power BI model: `work_orders[doc_id]` 1-to-many `extraction_errors[doc_id]`. The other three tables
stand alone.

## work_orders.csv (one row per PDF, 50 rows)

The final answer of the LLM path (LLM, retried if invalid, rules only if the LLM never gave valid JSON).

| Column | Type | Meaning |
|---|---|---|
| doc_id | text | `doc_001` ... `doc_050`; key to `extraction_errors` |
| layout | text | `form`, `table` or `narrative` (free-text email) |
| style | text | `seen` (rules were written on these templates) or `unseen` (new wording and formats) |
| source_file | text | PDF file name in `data/docs/` |
| work_order_id | text | `WO-YYYY-NNNNN` |
| date_reported | date | Date the equipment came in; blank if the document does not say |
| equipment_type | text | generator, forklift, utility truck, hvac unit, air compressor, water pump |
| serial_number | text | `SN-XXX-NNNNN`, normalised to upper case with dashes |
| failure_category | text | electrical, hydraulic, engine, cooling, tires_brakes |
| labor_hours | decimal | Repair labour hours (not the equipment hour meter) |
| parts_cost_usd | decimal | Parts cost in US dollars |
| priority | text | low, medium, high |
| warranty_claim | true/false | Repair claimed under warranty |
| extraction_method | text | `llm` (valid first try), `llm_retry` (valid after the error was sent back), `rules_fallback` |
| llm_attempts | integer | Model calls used for this document (1 to 3) |
| validation_errors | text | Pydantic errors from rejected attempts, `|`-separated; blank if none |
| seconds | decimal | Wall time for this document's extraction (0 when served from the cache) |
| text_chars | integer | Characters of text pulled from the PDF |

## field_accuracy.csv (one row per method x style x field)

| Column | Type | Meaning |
|---|---|---|
| method | text | `llm`, `rules` (regex baseline) or `hybrid` (LLM, nulls filled by rules) |
| style | text | `seen`, `unseen` or `all` |
| field | text | One of the 9 fields, or `ALL FIELDS` |
| tp | integer | Value given and correct |
| fp | integer | Value given but wrong, or given when the document does not state it |
| fn | integer | Value in the answer key but missed or wrong |
| n_cells | integer | Documents x fields scored |
| precision | decimal 0-1 | tp / (tp + fp) |
| recall | decimal 0-1 | tp / (tp + fn) |
| f1 | decimal 0-1 | Harmonic mean of precision and recall |

## extraction_errors.csv (one row per wrong field, all three methods)

| Column | Type | Meaning |
|---|---|---|
| method | text | As above |
| doc_id, layout, style | text | As in `work_orders` |
| field | text | Which field was wrong |
| expected | text | Value from the answer key (blank = document does not state it) |
| extracted | text | What the method returned (blank = null) |
| outcome | text | `wrong` (different value), `missed` (null but should have a value), `hallucinated` (a value the document does not contain) |

## nhtsa_recalls.csv (public NHTSA recalls API, real data)

| Column | Type | Meaning |
|---|---|---|
| campaign | text | NHTSA campaign number |
| make, model, model_year | text / integer | Vehicle queried |
| component_nhtsa | text | NHTSA's own component label (used as the answer key) |
| summary, consequence | text | NHTSA free text; the model sees only these two |
| category_from_nhtsa | text | `component_nhtsa` mapped to this project's categories (`/`-separated if several) |
| category_llm | text | Category the local model chose from the free text (`unknown` if it never gave a valid one) |
| defective_part_llm | text | Defective part named by the model, 1-4 words |
| match | true/false | `category_llm` is one of `category_from_nhtsa` |
| llm_attempts | integer | Model calls used |

## fda_recalls.csv (scraped from fda.gov, real data, no answer key)

| Column | Type | Meaning |
|---|---|---|
| recall_date | date | Date on the FDA listing |
| brand, product, product_type, company | text | As shown on the FDA page |
| reason | text | FDA's recall reason text (the model sees only this) |
| url | text | Link to the FDA recall notice |
| reason_category | text | undeclared_allergen, undeclared_drug_ingredient, microbial_contamination, chemical_contamination, foreign_material, quality_or_potency, labeling, other |
| hazard_agent | text | Allergen, microbe, chemical, drug or material named, if any |
| llm_attempts | integer | Model calls used |
