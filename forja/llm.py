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

        # 1h-TTL cache writes are reported in the nested usage.cache_creation
        # breakdown; the legacy flat field only covers 5m-TTL writes.
        nested = getattr(response.usage, "cache_creation", None)
        usage_extra = {
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_1h_tokens": getattr(nested, "ephemeral_1h_input_tokens", 0) or 0,
            "cache_creation_5m_tokens": getattr(nested, "ephemeral_5m_input_tokens", 0) or 0,
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


class OpenRouterClient:
    """OpenAI-compatible adapter for OpenRouter (any hosted model).

    - API key from OPENROUTER_API_KEY only; never logged or written anywhere.
    - Structured output via response_format json_schema (strict); if a call
      fails or returns empty content, retries once with a doubled token
      budget, then falls back to prompt-enforced JSON.
    - `provider_route` pins one upstream host (allow_fallbacks=false) so every
      arm runs on the same route.
    - Reports OpenRouter's own cost per call (logged as reported_cost_usd).
    - cached_prefix is accepted for interface parity but simply concatenated:
      explicit cache control is Anthropic-specific.
    """

    name = "openrouter"

    _URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, logger: RunLogger, model: str,
                 provider_route: str | None = None,
                 context_tokens: int = 1_000_000):
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ForjaModelError("OPENROUTER_API_KEY is not set")
        self._key = key
        self.model = model
        self.provider_route = provider_route
        self._logger = logger
        # Norwegian text ≈ 2.05 chars/token; keep generous headroom.
        self.chunk_char_budget = max(120_000, int((context_tokens - 80_000) * 1.9))

    def _post(self, payload: dict, timeout: float = 1200.0) -> dict:
        import urllib.error
        import urllib.request
        body = json.dumps(payload).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(4):
            req = urllib.request.Request(
                self._URL, data=body,
                headers={"Authorization": f"Bearer {self._key}",
                         "Content-Type": "application/json",
                         "X-Title": "forja-benchmark-v2"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                try:
                    detail = json.load(e)
                except Exception:
                    detail = {"error": {"message": e.read()[:300].decode(errors="replace")}}
                msg = (detail.get("error") or {}).get("message", str(e))
                if e.code in (402,) or "credit" in str(msg).lower():
                    raise ForjaModelError(f"OpenRouter credits exhausted: {msg}") from e
                if e.code in (429, 500, 502, 503, 520, 524) and attempt < 3:
                    time.sleep(5 * (attempt + 1) ** 2)
                    last_err = e
                    continue
                raise ForjaModelError(f"OpenRouter HTTP {e.code}: {msg}") from e
            except (TimeoutError, OSError) as e:
                if attempt < 3:
                    time.sleep(5 * (attempt + 1) ** 2)
                    last_err = e
                    continue
                raise ForjaModelError(f"OpenRouter connection failure: {e}") from e
        raise ForjaModelError(f"OpenRouter retries exhausted: {last_err}")

    def complete(self, *, task: str, system: str, user: str,
                 json_schema: dict | None = None, max_tokens: int = 4096,
                 tags: dict | None = None,
                 cached_prefix: str | None = None) -> ModelResult:
        full_prompt = (cached_prefix + "\n\n" + user) if cached_prefix else user

        def build(payload_max: int, use_schema: bool) -> dict:
            payload: dict[str, Any] = {
                "model": self.model,
                "max_tokens": payload_max,
                "usage": {"include": True},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": full_prompt
                        if use_schema or json_schema is None else
                        full_prompt + "\n\nSvar KUN med gyldig JSON etter dette "
                        "skjemaet, uten kodeblokker eller annen tekst:\n"
                        + json.dumps(json_schema)},
                ],
            }
            if self.provider_route:
                payload["provider"] = {"order": [self.provider_route],
                                       "allow_fallbacks": False}
            if json_schema is not None and use_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "strict": True,
                                    "schema": json_schema}}
            return payload

        start = time.perf_counter()
        # Reasoning models spend output budget on thinking: give headroom, and
        # on an empty completion retry once with double budget, then without
        # the schema constraint.
        attempts = [(max(max_tokens, 8000), True),
                    (max(max_tokens * 2, 16000), True),
                    (max(max_tokens * 2, 16000), False)]
        response: dict = {}
        text = ""
        for payload_max, use_schema in attempts:
            try:
                response = self._post(build(payload_max, use_schema))
            except ForjaModelError as e:
                latency = time.perf_counter() - start
                _log_call(self._logger, task=task, client_name=self.name,
                          model=self.model, system=system, user=full_prompt,
                          result_text="", parsed_ok=False, latency_s=latency,
                          input_tokens=None, output_tokens=None,
                          error=str(e), tags=tags)
                raise
            choice = (response.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            if text.strip():
                break
        latency = time.perf_counter() - start

        usage = response.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        parsed: Any | None = None
        parse_error: str | None = None
        if json_schema is not None:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned[cleaned.find("{"):cleaned.rfind("}") + 1]
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as e:
                parse_error = f"invalid JSON from model: {e}"
        _log_call(self._logger, task=task, client_name=self.name, model=self.model,
                  system=system, user=full_prompt, result_text=text,
                  parsed_ok=parse_error is None, latency_s=latency,
                  input_tokens=usage.get("prompt_tokens"),
                  output_tokens=usage.get("completion_tokens"),
                  error=parse_error, tags=tags,
                  reported_cost_usd=usage.get("cost"),
                  served_provider=response.get("provider"),
                  cache_read_input_tokens=details.get("cached_tokens") or 0,
                  cache_creation_input_tokens=details.get("cache_write_tokens") or 0)
        if parse_error:
            raise ForjaModelError(parse_error)
        return ModelResult(text=text, parsed_json=parsed, latency_s=latency,
                           input_tokens=usage.get("prompt_tokens"),
                           output_tokens=usage.get("completion_tokens"),
                           client_name=self.name, model=self.model)


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
