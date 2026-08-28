"""Run logging: model calls and intermediate pipeline decisions as JSONL.

One RunLogger per benchmark run. Two append-only streams:

- model_calls.jsonl — every LLM invocation: client, model, task, full prompt,
  full raw response, latency, token usage. Nothing is truncated; failures
  must be inspectable after the fact.
- decisions.jsonl — every intermediate pipeline decision: constraint
  exclusions, retrieval shortlists, score components, final gate actions.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class RunLogger:
    def __init__(self, run_dir: Path | str):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        self._model_calls_path = self.run_dir / "model_calls.jsonl"
        self._decisions_path = self.run_dir / "decisions.jsonl"
        # In-memory copies so tests and the evaluator can assert on log content.
        self.model_calls: list[dict] = []
        self.decisions: list[dict] = []

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _write(self, path: Path, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_model_call(self, **payload) -> None:
        record = {"seq": self._next_seq(), "ts": time.time(), **payload}
        self.model_calls.append(record)
        self._write(self._model_calls_path, record)

    def log_decision(self, stage: str, **payload) -> None:
        record = {"seq": self._next_seq(), "ts": time.time(), "stage": stage, **payload}
        self.decisions.append(record)
        self._write(self._decisions_path, record)


class NullLogger(RunLogger):
    """Logger that keeps records in memory only (for unit tests)."""

    def __init__(self):  # noqa: D107 — intentionally skips file setup
        self._lock = threading.Lock()
        self._seq = 0
        self._model_calls_path = None
        self._decisions_path = None
        self.model_calls = []
        self.decisions = []

    def _write(self, path, record) -> None:  # type: ignore[override]
        pass
