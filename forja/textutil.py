"""Deterministic text utilities (tokenization) shared across the harness.

Dependency rule: deterministic modules (pipeline/*, evaluation/*) may import
this; this module imports nothing from the LLM boundary.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-zA-ZæøåÆØÅéÉ0-9_+-]{3,}")

# Function words excluded from lexical scoring (Norwegian + a few English),
# plus job-ad boilerplate that carries no signal.
STOPWORDS = {
    "og", "som", "for", "med", "til", "det", "den", "der", "har", "kan", "vil",
    "skal", "ikke", "være", "blir", "hos", "oss", "din", "ditt", "dine", "våre",
    "vår", "vårt", "innen", "samt", "ved", "fra", "etter", "eller", "både",
    "the", "and", "with", "you", "our", "your", "will", "this", "that",
    "søker", "søkes", "stilling", "stillingen", "arbeid", "arbeidsoppgaver",
    "kvalifikasjoner", "erfaring", "krav", "ønskelig", "tilbyr",
}


def tokens(text: str) -> list[str]:
    """Lowercased word tokens, stopwords removed. Deterministic."""
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]
