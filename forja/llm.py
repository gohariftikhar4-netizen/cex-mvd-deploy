"""The LLM boundary. Everything that talks to a model lives behind ModelClient.

Two implementations:

- AnthropicClient — real calls through the official `anthropic` SDK
  (default model: claude-opus-5). Used when credentials are available.
- OfflineDeterministicClient — a deterministic, neutral stand-in used for
  tests/CI and for exercising the harness without network access. It is NOT
  a simulation of LLM strengths or weaknesses: for the baseline task it ranks
  jobs by plain lexical overlap with the candidate text, with no constraint
  awareness and no domain knowledge, working ONLY from the same prompt text a
  real model would receive. Offline results validate the harness; they are
  not evidence about the edge (see BENCHMARK.md).

Every call — real or offline — is logged in full to model_calls.jsonl.

Deliberate choice: server-side refusal fallbacks are NOT enabled. In a
benchmark, a refusal must surface as a logged failure of the named model, not
a silent switch to a different model.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .runlog import RunLogger
from .textutil import tokens as _tokens

DEFAULT_MODEL = os.environ.get("FORJA_MODEL", "claude-opus-5")

# Prompt-format conventions shared by prompt builders and the offline parser.
# The real model sees the same markers; they are ordinary readable text.
CANDIDATE_SECTION = "=== KANDIDAT ==="
JOBS_SECTION = "=== STILLINGSANNONSER ==="
JOB_BLOCK_PREFIX = "[STILLING "  # e.g. "[STILLING job_017]"


class ForjaModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelResult:
    text: str
    parsed_json: Any | None
    latency_s: float
    input_tokens: int | None
    output_tokens: int | None
    client_name: str
    model: str


class ModelClient(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        task: str,
        system: str,
        user: str,
        json_schema: dict | None = None,
        max_tokens: int = 4096,
    ) -> ModelResult: ...


def _log_call(logger: RunLogger, *, task: str, client_name: str, model: str,
              system: str, user: str, result_text: str, parsed_ok: bool,
              latency_s: float, input_tokens: int | None,
              output_tokens: int | None, error: str | None = None,
              tags: dict | None = None, **extra) -> None:
    logger.log_model_call(
        task=task,
        client=client_name,
        model=model,
        system=system,
        prompt=user,
        response=result_text,
        parsed_ok=parsed_ok,
        latency_s=round(latency_s, 4),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=error,
        tags=tags or {},
        **extra,
    )


# --------------------------------------------------------------------------
# Real client (Anthropic SDK)
# --------------------------------------------------------------------------


class AnthropicClient:
    name = "anthropic"

    def __init__(self, logger: RunLogger, model: str = DEFAULT_MODEL):
        try:
            import anthropic  # lazy: offline mode must not require the SDK
        except ImportError as e:
            raise ForjaModelError(
                "The 'anthropic' package is not installed. "
                "Install it (pip install anthropic) or run with --mode offline."
            ) from e
        self._anthropic = anthropic
        # Identity-linked API keys require the workspace the request acts in.
        headers = {}
        workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if workspace:
            headers["anthropic-workspace-id"] = workspace
        self._client = anthropic.Anthropic(
            timeout=1200.0, max_retries=3,
            default_headers=headers or None,
        )
        self.model = model
        self._logger = logger

    def complete(self, *, task: str, system: str, user: str,
                 json_schema: dict | None = None, max_tokens: int = 4096,
                 tags: dict | None = None,
                 cached_prefix: str | None = None) -> ModelResult:
        """One model call. `cached_prefix` is a stable text block placed before
        the volatile part with a 1h prompt-cache breakpoint — used for the
        shared jobs corpus so all candidates reuse it (cost accounting tracks
        cache reads/writes separately)."""
        if cached_prefix:
            content: Any = [
                {"type": "text", "text": cached_prefix,
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": user},
            ]
            full_prompt = cached_prefix + "\n\n" + user
        else:
            content = user
            full_prompt = user
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        if json_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": json_schema}
            }
        start = time.perf_counter()
        try:
            # Streaming: long inputs/outputs must not hit HTTP timeouts.
            with self._client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
        except self._anthropic.APIError as e:
            latency = time.perf_counter() - start
            _log_call(self._logger, task=task, client_name=self.name, model=self.model,
                      system=system, user=full_prompt, result_text="", parsed_ok=False,
                      latency_s=latency, input_tokens=None, output_tokens=None,
                      error=f"{type(e).__name__}: {e}", tags=tags)
            raise ForjaModelError(f"model call failed for task {task!r}: {e}") from e
        latency = time.perf_counter() - start

        usage_extra = {
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            msg = f"model refused task {task!r}" + (f" ({detail})" if detail else "")
            _log_call(self._logger, task=task, client_name=self.name, model=self.model,
                      system=system, user=full_prompt, result_text="", parsed_ok=False,
                      latency_s=latency,
                      input_tokens=response.usage.input_tokens,
                      output_tokens=response.usage.output_tokens,
                      error=msg, tags=tags, **usage_extra)
            raise ForjaModelError(msg)

        text = "".join(b.text for b in response.content if b.type == "text")
        parsed: Any | None = None
        parse_error: str | None = None
        if json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                parse_error = f"invalid JSON from structured output: {e}"
        _log_call(self._logger, task=task, client_name=self.name, model=self.model,
                  system=system, user=full_prompt, result_text=text,
                  parsed_ok=parse_error is None, latency_s=latency,
                  input_tokens=response.usage.input_tokens,
                  output_tokens=response.usage.output_tokens,
                  error=parse_error, tags=tags, **usage_extra)
        if parse_error:
            raise ForjaModelError(parse_error)
        return ModelResult(
            text=text, parsed_json=parsed, latency_s=latency,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            client_name=self.name, model=self.model,
        )


def live_capability() -> tuple[bool, str]:
    """Best-effort check whether real model calls can work in this environment."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic SDK not installed"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True, "API credentials found in environment"
    profile = Path.home() / ".config" / "anthropic"
    if profile.exists():
        return True, f"anthropic CLI profile directory found at {profile}"
    return False, "no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN and no CLI profile"


# --------------------------------------------------------------------------
# Offline deterministic client
# --------------------------------------------------------------------------

def split_sections(prompt: str) -> tuple[str, str]:
    """(candidate_part, jobs_part) regardless of section order — V2 prompts
    put the jobs corpus first (for prompt caching), V1 puts the candidate
    first; both must parse."""
    ci = prompt.find(CANDIDATE_SECTION)
    ji = prompt.find(JOBS_SECTION)
    cand = jobs = ""
    if ci >= 0:
        end = ji if ji > ci else len(prompt)
        cand = prompt[ci + len(CANDIDATE_SECTION):end]
    if ji >= 0:
        end = ci if ci > ji else len(prompt)
        jobs = prompt[ji + len(JOBS_SECTION):end]
    return cand, jobs


def split_job_blocks(prompt: str) -> list[tuple[str, str]]:
    """Split the jobs section of a prompt into (job_id, block_text) pairs.
    Blocks are introduced by the shared '[STILLING <id>]' convention."""
    _, jobs_part = split_sections(prompt)
    if not jobs_part:
        jobs_part = prompt
    blocks: list[tuple[str, str]] = []
    current_id: str | None = None
    current_lines: list[str] = []
    for line in jobs_part.splitlines():
        if line.startswith(JOB_BLOCK_PREFIX):
            if current_id is not None:
                blocks.append((current_id, "\n".join(current_lines)))
            current_id = line[len(JOB_BLOCK_PREFIX):].rstrip("]").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_id is not None:
        blocks.append((current_id, "\n".join(current_lines)))
    return blocks


class OfflineDeterministicClient:
    """Neutral deterministic stand-in for a general-purpose LLM (see module docstring)."""

    name = "offline-deterministic"
    model = "offline-lexical-v1"

    def __init__(self, logger: RunLogger):
        self._logger = logger

    def complete(self, *, task: str, system: str, user: str,
                 json_schema: dict | None = None, max_tokens: int = 4096,
                 tags: dict | None = None,
                 cached_prefix: str | None = None) -> ModelResult:
        if cached_prefix:
            user = cached_prefix + "\n\n" + user
        start = time.perf_counter()
        parsed: Any | None = None
        if task == "baseline.advise":
            text = self._baseline_advise(user)
        elif task == "forja.profile_enrichment":
            # Conservative offline behavior: suggest nothing rather than guess.
            parsed = {"suggested_skills": [], "notes": "offline mode: no enrichment"}
            text = json.dumps(parsed, ensure_ascii=False)
        elif task in ("v2.rank_chunk", "v2.merge", "v2.rerank"):
            parsed = self._lexical_rank(user)
            text = json.dumps(parsed, ensure_ascii=False)
        elif task == "v2.critique":
            # Offline stand-in: no revisions — echo an empty change set.
            parsed = {"items": []}
            text = json.dumps(parsed)
        elif task == "v2.verify":
            parsed = {"verdicts": []}  # keep everything (no strikes offline)
            text = json.dumps(parsed)
        elif task == "v2.soft_pref":
            ids = [job_id for job_id, _ in split_job_blocks(user)]
            parsed = {"preferences": [
                {"job_id": j, "fit": 0.5, "claim": "offline: nøytral preferanse",
                 "quote": ""} for j in ids
            ]}
            text = json.dumps(parsed, ensure_ascii=False)
        else:
            raise ForjaModelError(f"offline client has no handler for task {task!r}")
        latency = time.perf_counter() - start
        _log_call(self._logger, task=task, client_name=self.name, model=self.model,
                  system=system, user=user, result_text=text, parsed_ok=True,
                  latency_s=latency, input_tokens=None, output_tokens=None, tags=tags)
        return ModelResult(text=text, parsed_json=parsed, latency_s=latency,
                           input_tokens=None, output_tokens=None,
                           client_name=self.name, model=self.model)

    # -- shared lexical scoring over the same prompt text a real model sees --

    def _scored_blocks(self, prompt: str) -> list[tuple[float, str, list[str]]]:
        if CANDIDATE_SECTION not in prompt or JOBS_SECTION not in prompt:
            raise ForjaModelError("prompt missing expected sections")
        candidate_part, _ = split_sections(prompt)
        candidate_tokens = set(_tokens(candidate_part))
        scored = []
        for job_id, body in split_job_blocks(prompt):
            job_tokens = _tokens(body)
            if not job_tokens:
                continue
            overlap = [t for t in dict.fromkeys(job_tokens) if t in candidate_tokens]
            score = len(overlap) / math.sqrt(len(set(job_tokens)))
            scored.append((score, job_id, overlap[:5]))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return scored

    def _lexical_rank(self, prompt: str) -> dict:
        items = []
        for score, job_id, overlap in self._scored_blocks(prompt)[:50]:
            quote = overlap[0] if overlap else ""
            items.append({
                "job_id": job_id,
                "score": round(score * 20, 2),
                "claims": ([{"claim": f"Ordoverlapp med kandidatprofilen: {quote}",
                             "source": "candidate", "quote": quote}] if quote else []),
            })
        return {"items": items}

    def _baseline_advise(self, prompt: str) -> str:
        lines = ["Her er mine anbefalinger, rangert:"]
        for rank, (score, job_id, overlap) in enumerate(self._scored_blocks(prompt)[:10], start=1):
            reason = ", ".join(overlap) if overlap else "generell profilmatch"
            lines.append(f"{rank}. {job_id} – god match på: {reason}.")
        return "\n".join(lines)
