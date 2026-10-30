"""Shared fixtures. Documents are generated into a temp folder; the repo's data/ folder is never touched."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from generator.make_docs import generate


@pytest.fixture(scope="session")
def docs(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("docs")
    generate(seed=42, n=50, out=out)
    return {"dir": out, "key": pd.read_csv(out / "answer_key.csv")}


class FakeLLM:
    """Stands in for OllamaClient: returns queued answers in order and records every message list it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[list[dict]] = []

    def chat(self, messages, schema=None):
        self.sent.append([dict(m) for m in messages])
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a)


@pytest.fixture
def fake_llm():
    return FakeLLM


def first_doc(docs: dict, layout: str, style: str = "seen") -> tuple[Path, pd.Series]:
    k = docs["key"]
    row = k[(k.layout == layout) & (k["style"] == style)].iloc[0]
    return docs["dir"] / f"{row.doc_id}.pdf", row
