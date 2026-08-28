from forja.pipeline import retrieval
from forja.schemas import Job
from tests.conftest import job_dict


def _jobs():
    return [
        Job.from_dict(job_dict(
            id="job_910", title="Sykepleier", sector="helse",
            requirements={"must_have_skills": ["klinisk_sykepleie"]},
            description="Sykepleier til sengepost med medikamenthåndtering og pasientveiledning.")),
        Job.from_dict(job_dict(
            id="job_911", title="Backend-utvikler",
            requirements={"must_have_skills": ["python", "kubernetes"]},
            description="Utvikler med python og kubernetes i skyen.")),
        Job.from_dict(job_dict(
            id="job_912", title="Frisør", sector="handel",
            requirements={"must_have_skills": ["frisering"]},
            description="Frisør med svennebrev til salong.")),
    ]


def test_relevant_document_ranks_first():
    index = retrieval.build_index(_jobs())
    ranked = retrieval.retrieve(index, "erfaren sykepleier medikamenthåndtering pasientveiledning", k=3)
    assert ranked[0][0] == "job_910"
    assert ranked[0][1] > ranked[-1][1]


def test_retrieval_is_deterministic():
    jobs = _jobs()
    r1 = retrieval.retrieve(retrieval.build_index(jobs), "python kubernetes utvikler", k=3)
    r2 = retrieval.retrieve(retrieval.build_index(jobs), "python kubernetes utvikler", k=3)
    assert r1 == r2
    assert r1[0][0] == "job_911"


def test_query_with_no_overlap_scores_zero():
    index = retrieval.build_index(_jobs())
    ranked = retrieval.retrieve(index, "xyzzy quux", k=3)
    assert all(score == 0.0 for _, score in ranked)
