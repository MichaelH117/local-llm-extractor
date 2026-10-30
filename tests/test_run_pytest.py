"""Regression test: a pytest run that ends in errors must never be recorded as a clean '0 passed, 0 failed'."""

from __future__ import annotations

import subprocess

from pipeline import run

COLLECTION_ERROR = """
==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_extract.py ______________________
ImportError while importing test module 'tests/test_extract.py'.
E   ModuleNotFoundError: No module named 'pydantic'
=========================== short test summary info ============================
ERROR tests/test_extract.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.41s
"""


def _fake_pytest(monkeypatch, stdout: str, returncode: int) -> None:
    monkeypatch.setattr(run.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout=stdout, stderr=""))


def test_collection_error_is_not_ok(monkeypatch):
    _fake_pytest(monkeypatch, COLLECTION_ERROR, returncode=2)
    t = run.run_pytest()
    assert isinstance(t, dict) and t.get("ok") is False, f"a suite that never ran was recorded as ok: {t!r}"
    assert t["errors"] == 1 and t["passed"] == 0
    assert "NOT RUN CLEANLY" in run.pytest_row(t, "2026-10-30")[1]


def test_error_with_exit_code_1_is_still_not_ok(monkeypatch):
    _fake_pytest(monkeypatch, "..F.E\n1 failed, 3 passed, 1 error in 2.0s\n", returncode=1)
    t = run.run_pytest()
    assert t.get("ok") is False and (t["passed"], t["failed"], t["errors"]) == (3, 1, 1)


def test_clean_run_is_ok(monkeypatch):
    _fake_pytest(monkeypatch, "....\n26 passed, 1 skipped, 1 xfailed in 30.1s\n", returncode=0)
    t = run.run_pytest()
    assert t["ok"] is True and (t["passed"], t["failed"], t["skipped"], t["xfailed"]) == (26, 0, 1, 1)
    assert run.pytest_row(t, "2026-10-30")[1] == "**26 passed, 0 failed**"
