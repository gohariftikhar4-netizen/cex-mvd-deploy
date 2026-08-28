"""Semantic-ish retrieval: deterministic TF-IDF cosine over job text.

This is the deterministic stand-in for embedding-based retrieval (a real
deployment would swap in an embedding index behind the same interface; the
lexical approximation is a documented benchmark limitation). Pure stdlib,
fully deterministic, no LLM involvement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..schemas import Job
from ..textutil import tokens


def job_document(job: Job) -> str:
    """The text a retrieval index sees for a job (ad text + structured fields)."""
    req = job.requirements
    parts = [
        job.title,
        job.employer,
        job.sector,
        job.location_city,
        job.work_mode,
        " ".join(req.must_have_skills),
        " ".join(req.nice_to_have_skills),
        job.description,
    ]
    return "\n".join(parts)


@dataclass(frozen=True)
class TfIdfIndex:
    job_ids: tuple[str, ...]
    vectors: tuple[dict[str, float], ...]  # normalized tf-idf vectors
    idf: dict[str, float]


def build_index(jobs: list[Job]) -> TfIdfIndex:
    docs = [tokens(job_document(j)) for j in jobs]
    n = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    idf = {term: math.log((1 + n) / (1 + count)) + 1.0 for term, count in df.items()}

    vectors = []
    for doc in docs:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        vec = {term: (1 + math.log(count)) * idf[term] for term, count in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({term: v / norm for term, v in vec.items()})

    return TfIdfIndex(
        job_ids=tuple(j.id for j in jobs),
        vectors=tuple(vectors),
        idf=idf,
    )


def retrieve(index: TfIdfIndex, query_text: str, k: int) -> list[tuple[str, float]]:
    """Top-k (job_id, cosine_score), deterministically ordered (score desc, id asc)."""
    q_tokens = tokens(query_text)
    tf: dict[str, int] = {}
    for term in q_tokens:
        tf[term] = tf.get(term, 0) + 1
    q_vec = {
        term: (1 + math.log(count)) * index.idf.get(term, 0.0)
        for term, count in tf.items()
        if term in index.idf
    }
    norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    q_vec = {t: v / norm for t, v in q_vec.items()}

    scored = []
    for job_id, vec in zip(index.job_ids, index.vectors):
        score = sum(w * vec.get(t, 0.0) for t, w in q_vec.items())
        scored.append((job_id, round(score, 6)))
    scored.sort(key=lambda s: (-s[1], s[0]))
    return scored[:k]
