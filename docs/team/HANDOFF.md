# Handoff log

## 2026-10-30, Data Engineer: pytest error counting
- Did: moved the inline pytest call into run_pytest() with -p no:cacheprovider; results.json now stores counts (passed/failed/skipped/errors/xfailed, ok) instead of a string. ok=False on any error or an exit code other than 0/1; README row says DID NOT RUN CLEANLY and the pipeline exits non-zero. Old string results.json still renders the same row.
- Files: pipeline/run.py, tests/test_run_pytest.py (new).
- Checks: regression test fails against the old logic ("recorded as ok: '0 passed, 0 failed'") and passes with the fix. pytest -m "not integration": 34 passed, 1 deselected (35 collected = 32 + 3 new). The integration test was not run: it would load gemma3:4b and only ~3 GB RAM was free.
- Open: README still shows 32 passed from the 2026-10-30 pipeline run; the next python -m pipeline.run --test (Ollama up) records 35.
- Next: QA Engineer.

## 2026-10-30, Design Lead: MANILA results site (OVERNIGHT_PLAN step 5)
- Did: built `site/` (Overview story, Watch the AI read, Where it slipped, Privacy with a draft WISP paragraph, Glossary) in Hanson Labs / MANILA with the living wallpaper (story pages on; reader off by default), logo, favicon and footer lockup. `site/build_data.py` runs as Quarto pre-render, recomputes every number from docs/results.json + exports/ (and recounts from the answer key when data/docs exists), writes `_variables.yml`, `_gen/*.html`, `data/site-data.js` and copies the 50 SYNTHETIC PDFs; it exits 1 on any mismatch. README has a Results site section. Also fixed dev-team/brand/tokens/build_tokens.py (one font family per Streamlit URL) and re-ran it.
- Files: site/** (new), README.md, .gitignore, docs/team/DESIGN_REVIEW.md (new), docs/img/site/*.png (new); outside this repo: dev-team/brand/tokens/build_tokens.py + streamlit/*.toml + themes/*.css (regenerated, palette unchanged).
- Checks: build_tokens 170 checks, 0 failed. quarto render site: 5/5 pages, 0 warnings. Planted mismatch in field_accuracy.csv -> build_data exit 1 (4 checks fired), restored. Headless Chrome: 33 audit runs (5 pages x 1440/1280x720/640/375/forced-colors/reduced-motion + dark + 2 reader states): 0 errors, 0 overflow, 0 small targets, 0 broken labels.
- Open: titles not yet reader-tested; no network capture; no real phone/screen reader test; GitHub Pages publishing not set up (no push).
- Next: QA Engineer (or UX researcher for the takeaway-title restatement test).
