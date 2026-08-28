"""Candidate profiling: structured skills with provenance + retrieval query.

Deterministic core: declared skills plus conservative alias extraction from
the candidate's own free text and work history.

Optional LLM enrichment ("LLM proposes, deterministic code disposes"): the
model may SUGGEST additional canonical skills it can justify with a literal
quote from the candidate's free text. A suggestion is accepted only if
  1. the skill exists in the taxonomy, and
  2. the quoted evidence actually appears verbatim (case-insensitive) in the
     candidate's text.
Rejected suggestions are logged, never used. Enrichment can never touch hard
constraints — profiling has no access to them by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import taxonomy
from ..llm import ModelClient
from ..runlog import RunLogger
from ..schemas import Candidate

PROVENANCE_DECLARED = "declared"
PROVENANCE_EXTRACTED = "extracted_from_own_text"
PROVENANCE_LLM = "llm_suggested_validated"

_ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "suggested_skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["skill", "evidence_quote"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["suggested_skills", "notes"],
    "additionalProperties": False,
}

_ENRICHMENT_SYSTEM = (
    "Du er en kompetansekartlegger. Du får en kandidats egen tekst og listen "
    "over kompetanser som allerede er registrert. Foreslå KUN kompetanser fra "
    "den kontrollerte listen som teksten dokumenterer, med et ordrett sitat "
    "fra teksten som bevis. Ikke gjett. Ikke foreslå noe som allerede er "
    "registrert."
)


@dataclass(frozen=True)
class CandidateProfile:
    candidate: Candidate
    # canonical skill -> provenance
    effective_skills: dict[str, str] = field(default_factory=dict)
    query_text: str = ""

    @property
    def skill_set(self) -> set[str]:
        return set(self.effective_skills)


def _own_text(candidate: Candidate) -> str:
    parts = [candidate.free_text]
    for w in candidate.work_history:
        parts.append(f"{w.title} – {w.employer}: {w.description}")
    return "\n".join(parts)


def _fold(text: str) -> str:
    """Lowercase + fold Norwegian letters so ascii skill ids can match."""
    return (text.lower()
            .replace("æ", "ae").replace("ø", "o").replace("å", "a")
            .replace("é", "e"))


def _phrase_present(phrase: str, folded_text: str) -> bool:
    """Whole-word/phrase match on folded text. Substring matches are forbidden:
    'varelogistikk' must never grant the skill 'gis'."""
    pattern = r"(?<![a-z0-9'])" + re.escape(phrase) + r"(?![a-z0-9'])"
    return re.search(pattern, folded_text) is not None


def _extract_alias_skills(text: str) -> set[str]:
    """Conservative deterministic extraction: known aliases / canonical names
    appearing as whole words in the candidate's own text."""
    folded = _fold(text)
    found: set[str] = set()
    for alias, canonical in taxonomy.SKILL_ALIASES.items():
        if _phrase_present(_fold(alias), folded):
            found.add(canonical)
    for skill in taxonomy.SKILLS:
        if _phrase_present(skill.replace("_", " "), folded) or _phrase_present(skill, folded):
            found.add(skill)
    return found


def build_profile(
    candidate: Candidate,
    logger: RunLogger,
    model_client: ModelClient | None = None,
) -> CandidateProfile:
    effective: dict[str, str] = {s: PROVENANCE_DECLARED for s in candidate.skills}

    own_text = _own_text(candidate)
    for skill in sorted(_extract_alias_skills(own_text)):
        effective.setdefault(skill, PROVENANCE_EXTRACTED)

    llm_accepted: list[dict] = []
    llm_rejected: list[dict] = []
    if model_client is not None:
        result = model_client.complete(
            task="forja.profile_enrichment",
            system=_ENRICHMENT_SYSTEM,
            user=(
                f"Registrerte kompetanser: {sorted(effective)}\n\n"
                f"Kontrollert liste: {taxonomy.SKILLS}\n\n"
                f"Kandidatens tekst:\n{own_text}"
            ),
            json_schema=_ENRICHMENT_SCHEMA,
            max_tokens=2000,
        )
        suggestions = (result.parsed_json or {}).get("suggested_skills", [])
        low_text = own_text.lower()
        for s in suggestions:
            skill = s.get("skill", "")
            quote = s.get("evidence_quote", "")
            if not taxonomy.is_known_skill(skill):
                llm_rejected.append({**s, "rejected_because": "unknown skill"})
            elif quote.lower() not in low_text:
                llm_rejected.append({**s, "rejected_because": "evidence quote not found in candidate text"})
            elif skill in effective:
                llm_rejected.append({**s, "rejected_because": "already present"})
            else:
                effective[skill] = PROVENANCE_LLM
                llm_accepted.append(s)

    logger.log_decision(
        "profiling",
        candidate_id=candidate.id,
        effective_skills=dict(sorted(effective.items())),
        llm_accepted=llm_accepted,
        llm_rejected=llm_rejected,
    )

    query_parts = [
        " ".join(w.title for w in candidate.work_history),
        " ".join(sorted(effective)),
        " ".join(candidate.preferred_sectors),
        candidate.free_text,
    ]
    return CandidateProfile(
        candidate=candidate,
        effective_skills=dict(sorted(effective.items())),
        query_text="\n".join(query_parts),
    )
