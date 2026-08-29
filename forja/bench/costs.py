"""Model cost accounting (secondary metrics: cost, tokens, calls, latency).

Prices are USD per million tokens for the Claude API (cached 2026-06; verify
against current pricing before quoting results externally)."""

from __future__ import annotations

PRICING_PER_MTOK = {
    # model id: (input $/MTok, output $/MTok)
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Prompt-cache multipliers on the input price: 1h-TTL writes cost 2x base
# input; cache reads cost 0.1x base input.
CACHE_WRITE_1H_MULT = 2.0
CACHE_READ_MULT = 0.1


def call_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None,
                  cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float | None:
    if model not in PRICING_PER_MTOK or input_tokens is None or output_tokens is None:
        return None
    p_in, p_out = PRICING_PER_MTOK[model]
    return (input_tokens / 1e6 * p_in
            + output_tokens / 1e6 * p_out
            + cache_write_tokens / 1e6 * p_in * CACHE_WRITE_1H_MULT
            + cache_read_tokens / 1e6 * p_in * CACHE_READ_MULT)


def aggregate_usage(model_calls: list[dict]) -> dict:
    """Aggregate logger model-call records by (workflow, candidate_id) tags."""
    out: dict[tuple[str, str], dict] = {}
    for call in model_calls:
        tags = call.get("tags") or {}
        key = (tags.get("workflow", "?"), tags.get("candidate_id", "?"))
        agg = out.setdefault(key, {
            "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_write_tokens": 0, "cache_read_tokens": 0,
            "latency_s": 0.0, "cost_usd": 0.0, "tokens_known": True,
        })
        agg["model_calls"] += 1
        agg["latency_s"] = round(agg["latency_s"] + (call.get("latency_s") or 0.0), 4)
        if call.get("input_tokens") is None:
            agg["tokens_known"] = False
        else:
            cache_w = call.get("cache_creation_input_tokens") or 0
            cache_r = call.get("cache_read_input_tokens") or 0
            agg["input_tokens"] += call["input_tokens"]
            agg["output_tokens"] += call.get("output_tokens") or 0
            agg["cache_write_tokens"] += cache_w
            agg["cache_read_tokens"] += cache_r
            cost = call_cost_usd(call.get("model", ""), call["input_tokens"],
                                 call.get("output_tokens"), cache_w, cache_r)
            if cost is not None:
                agg["cost_usd"] = round(agg["cost_usd"] + cost, 6)
    return {f"{wf}::{cand}": v for (wf, cand), v in sorted(out.items())}
