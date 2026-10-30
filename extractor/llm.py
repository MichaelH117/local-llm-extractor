"""A tiny client for Ollama's local HTTP API. It refuses to talk to anything that is not this machine."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_MODEL = "gemma3:4b"


class NonLocalEndpointError(ValueError):
    pass


class OllamaClient:
    """POST /api/chat with a JSON schema in `format`, temperature 0 and a fixed seed for repeatable answers.

    Responses are cached on disk by a hash of (model, messages, schema, options), so a re-run of the pipeline
    gives the same answers instantly. Delete the cache folder (or pass cache_dir=None) to call the model again.
    """

    def __init__(self, base_url: str = "http://localhost:11434", model: str = DEFAULT_MODEL,
                 cache_dir: Path | None = None, timeout: float = 600, seed: int = 7):
        host = urlparse(base_url).hostname
        if host not in LOCAL_HOSTS:
            raise NonLocalEndpointError(f"refusing to send documents to non-local host {host!r}")
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self.options = {"temperature": 0, "seed": seed, "num_ctx": 4096}
        self.cache_dir = cache_dir
        self.calls = 0         # real model calls (cache misses)
        self.seconds = 0.0     # wall time spent in real model calls

    def available(self) -> bool:
        try:
            tags = requests.get(f"{self.base_url}/api/tags", timeout=5).json()
        except requests.RequestException:
            return False
        return any(m["name"] == self.model for m in tags.get("models", []))

    def chat(self, messages: list[dict], schema: dict | None = None) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False, "options": self.options}
        if schema is not None:
            payload["format"] = schema
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))["content"]
        t0 = time.perf_counter()
        r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        r.raise_for_status()
        content = r.json()["message"]["content"]
        self.calls += 1
        self.seconds += time.perf_counter() - t0
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({"model": self.model, "content": content}), encoding="utf-8")
        return content
