# Handoff log

## 2026-10-30, Data Engineer: pytest error counting
- Did: moved the inline pytest call into run_pytest() with -p no:cacheprovider; results.json now stores counts (passed/failed/skipped/errors/xfailed, ok) instead of a string. ok=False on any error or an exit code other than 0/1; README row says DID NOT RUN CLEANLY and the pipeline exits non-zero. Old string results.json still renders the same row.
- Files: pipeline/run.py, tests/test_run_pytest.py (new).
- Checks: regression test fails against the old logic ("recorded as ok: '0 passed, 0 failed'") and passes with the fix. pytest -m "not integration": 34 passed, 1 deselected (35 collected = 32 + 3 new). The integration test was not run: it would load gemma3:4b and only ~3 GB RAM was free.
- Open: README still shows 32 passed from the 2026-10-30 pipeline run; the next python -m pipeline.run --test (Ollama up) records 35.
- Next: QA Engineer.
