"""Final recommendation assembly with a defense-in-depth constraint gate.

The gate re-checks hard constraints on every recommendation immediately
before it is emitted. Upstream filtering should make this a no-op; if
anything ever reaches the gate with a violation (a bug, or an LLM-influenced
step misbehaving), the job is dropped and the event is logged as
`final_gate_blocked`. An LLM can therefore never cause a constraint-violating
recommendation to be emitted, no matter what it outputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..runlog import RunLogger
from ..schemas import Job
from . import constraints
from .gaps import Gap, analyze
from .matching import MIN_MUST_HAVE_COVERAGE, MIN_TOTAL_SCORE, MatchResult
from .profiling import CandidateProfile

MAX_RECOMMENDATIONS = 10


@dataclass(frozen=True)
class Recommendation:
    job_id: str
    rank: int
    score: float
    evidence: tuple[dict, ...]          # serialized EvidenceItems
    constraint_report: dict             # serialized ConstraintReport (final gate)
    gaps: tuple[dict, ...]
    next_actions: tuple[str, ...]
    score_components: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "rank": self.rank,
            "score": self.score,
            "evidence": list(self.evidence),
            "constraint_report": self.constraint_report,
            "gaps": list(self.gaps),
            "next_actions": list(self.next_actions),
            "score_components": list(self.score_components),
        }


def assemble(
    profile: CandidateProfile,
    matches: list[MatchResult],
    jobs_by_id: dict[str, Job],
    logger: RunLogger,
) -> list[Recommendation]:
    candidate = profile.candidate

    ranked = sorted(matches, key=lambda m: (-m.total_score, m.job_id))

    recommendations: list[Recommendation] = []
    for match in ranked:
        if len(recommendations) >= MAX_RECOMMENDATIONS:
            break
        if match.must_have_coverage < MIN_MUST_HAVE_COVERAGE:
            logger.log_decision(
                "recommend.skip_below_coverage_bar",
                candidate_id=candidate.id, job_id=match.job_id,
                must_have_coverage=match.must_have_coverage,
                bar=MIN_MUST_HAVE_COVERAGE,
            )
            continue
        if match.total_score < MIN_TOTAL_SCORE:
            logger.log_decision(
                "recommend.skip_below_score_bar",
                candidate_id=candidate.id, job_id=match.job_id,
                total_score=match.total_score, bar=MIN_TOTAL_SCORE,
            )
            continue

        job = jobs_by_id[match.job_id]

        # Defense in depth: nothing gets out with a hard-constraint violation.
        report = constraints.check(candidate, job)
        if not report.passed:
            logger.log_decision(
                "recommend.final_gate_blocked",
                candidate_id=candidate.id, job_id=job.id,
                violations=[v.to_dict() for v in report.violations],
            )
            continue

        gap_list: list[Gap] = analyze(profile, job, match)
        next_actions = tuple(g.next_action for g in gap_list) or (
            "Søk direkte — profilen dekker kravene i annonsen.",
        )

        recommendations.append(Recommendation(
            job_id=job.id,
            rank=len(recommendations) + 1,
            score=match.total_score,
            evidence=tuple(e.to_dict() for e in match.all_evidence()),
            constraint_report=report.to_dict(),
            gaps=tuple(g.to_dict() for g in gap_list),
            next_actions=next_actions,
            score_components=tuple(c.to_dict() for c in match.components),
        ))

    logger.log_decision(
        "recommend.final",
        candidate_id=candidate.id,
        recommended=[r.job_id for r in recommendations],
    )
    return recommendations
