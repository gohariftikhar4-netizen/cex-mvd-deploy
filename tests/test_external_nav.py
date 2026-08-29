"""Tests for the external NAV validation track.

These test the ADAPTER LOGIC only — no network, no live NAV calls. The
frozen V2 machinery must remain untouched by this package (asserted here).
"""

import ast
from pathlib import Path

import pytest

from forja.external.nav.analyze import classify_ad, plain_text, structured_completeness
from forja.external.nav.to_jobs import nav_ad_to_job
from forja.schemas import BENCHMARK_TODAY, load_candidates_v2

ROOT = Path(__file__).parent.parent / "forja"


def _ad(description_html: str, **over) -> dict:
    base = {
        "uuid": "test-uuid-1",
        "status": "ACTIVE",
        "title": "Testtittel",
        "jobtitle": "Testtittel",
        "employer_name": "Testbedrift AS",
        "description_html": description_html,
        "engagementtype": "Fast",
        "extent": "Heltid",
        "sector": "Privat",
        "positioncount": 1,
        "starttime": "",
        "application_due": "18.09.2026",
        "published": "2026-08-29T06:00:00+02:00",
        "expires": "2026-09-18T00:00:00+02:00",
        "work_locations": [{"country": "NORGE", "city": "OSLO", "county": "OSLO",
                            "municipal": "OSLO", "postalCode": "0150", "address": None}],
        "occupation_categories": [{"level1": "Industri og produksjon", "level2": "Trevarearbeid"}],
        "category_list": [],
        "source": "Test",
        "raw": {},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- analyzer

def test_plain_text_strips_html_and_entities():
    assert plain_text("<div><strong>Hei</strong>&nbsp;p&aring; deg<br/></div>") == "Hei på deg"
    assert plain_text(None) == ""


def test_text_only_constraint_detected():
    """The core measurement: a requirement stated ONLY in the ad text."""
    ad = _ad("<p>Vi søker sjåfør. Førerkort klasse C er et absolutt krav. "
             "Arbeidet innebærer nattevakter i turnus.</p>")
    cls = classify_ad(ad)
    assert cls["drivers_license"]["state"] == "text_only"
    assert cls["working_hours_night"]["state"] in ("text_only", "ambiguous")
    assert cls["drivers_license"]["snippets"]


def test_hedged_requirement_marked_ambiguous():
    ad = _ad("<p>Noe reisevirksomhet med overnatting kan forekomme i perioder.</p>")
    cls = classify_ad(ad)
    assert cls["travel"]["state"] == "ambiguous"
    assert cls["travel"]["hedged"] is True


def test_structured_dimension_reported_as_both_when_also_in_text():
    ad = _ad("<p>Dette er en 80 % deltidsstilling.</p>", extent="Deltid")
    cls = classify_ad(ad)
    assert cls["extent_parttime"]["state"] == "both"


def test_absent_dimension_is_not_specified():
    ad = _ad("<p>Vi søker en hyggelig kollega til teamet vårt.</p>")
    cls = classify_ad(ad)
    assert cls["drivers_license"]["state"] == "not_specified"
    assert cls["physical"]["state"] == "not_specified"


def test_structured_completeness_flags_missing_fields():
    comp = structured_completeness(_ad("<p>" + "x" * 300 + "</p>", extent="",
                                       engagementtype=""))
    assert comp["extent"] is False and comp["engagementtype"] is False
    assert comp["employer_name"] is True and comp["description_nonempty"] is True


# ---------------------------------------------------------------- to_jobs

def test_requirements_are_never_invented_from_text():
    """The honesty rule: even a text FULL of requirements must produce empty
    structured requirements, because NAV publishes none."""
    ad = _ad("<p>Krav: fagbrev elektriker, førerkort klasse B, norsk C1, "
             "autorisasjon som sykepleier, tunge løft, nattevakt.</p>")
    job, notes = nav_ad_to_job(ad, "nav_00001", "Oslo")
    r = job.requirements
    assert r.must_have_skills == () and r.nice_to_have_skills == ()
    assert r.certifications_required == ()
    assert r.driving_license_required is None
    assert r.norwegian_min_level is None and r.english_min_level is None
    assert r.physical_demands == ()
    assert r.requires_norwegian_citizenship is False
    assert job.shifts == ()          # unstated, not assumed day-only
    assert job.salary_nok_min is None


def test_real_text_reaches_the_model_via_description():
    ad = _ad("<p>Førerkort klasse C er et absolutt krav.</p>")
    job, _ = nav_ad_to_job(ad, "nav_00002", "Oslo")
    assert "Førerkort klasse C" in job.description
    assert "Omfang: Heltid" in job.description  # NAV structured facts included


def test_deadline_parsed_from_norwegian_format():
    job, _ = nav_ad_to_job(_ad("<p>x</p>", application_due="18.09.2026"), "nav_3", "Oslo")
    assert job.application_deadline == "2026-09-18"
    assert not job.deadline_passed()  # BENCHMARK_TODAY is 2026-09-01


def test_expired_deadline_detected():
    job, _ = nav_ad_to_job(_ad("<p>x</p>", application_due="01.08.2026"), "nav_4", "Oslo")
    assert job.application_deadline == "2026-08-01"
    assert job.deadline_passed()


def test_unmapped_location_falls_back_and_is_recorded():
    ad = _ad("<p>x</p>", work_locations=[{"city": "SVOLVÆR", "county": "NORDLAND"}])
    job, notes = nav_ad_to_job(ad, "nav_5", "Bergen")
    assert job.location_city == "Bergen"          # candidate's own city
    assert notes["geography_fallback_used"] is True
    assert notes["nav_city"] == "SVOLVÆR"


def test_known_county_maps_without_fallback():
    ad = _ad("<p>x</p>", work_locations=[{"city": "MOSJØEN", "county": "TRØNDELAG"}])
    job, notes = nav_ad_to_job(ad, "nav_6", "Oslo")
    assert job.location_city == "Trondheim"
    assert notes["geography_fallback_used"] is False


def test_deltid_percentage_read_from_text_not_invented():
    ad = _ad("<p>Stillingen er en 60 % stilling.</p>", extent="Deltid")
    job, _ = nav_ad_to_job(ad, "nav_7", "Oslo")
    assert job.percent_position == 60
    # No figure in text -> documented 50% assumption, flagged in notes
    job2, notes2 = nav_ad_to_job(_ad("<p>Deltid.</p>", extent="Deltid"), "nav_8", "Oslo")
    assert job2.percent_position == 50 and notes2["percent_assumed"] is True


def test_real_ads_are_valid_jobs_for_all_v2_candidate_cities():
    """Adapter output must satisfy the frozen Job schema for every candidate."""
    for cand in load_candidates_v2():
        job, _ = nav_ad_to_job(_ad("<p>Test</p>"), "nav_9", cand.location_city)
        assert job.location_city in (cand.location_city, "Oslo")


# ------------------------------------------------- separation from frozen V2

def test_external_package_never_imported_by_frozen_benchmark():
    """The V2 corpus generator and runner must not depend on external data."""
    for path in sorted((ROOT / "bench").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "external" not in src or "forja.external" not in src, \
            f"{path} imports the external track"


def test_external_module_never_imports_v2_gold_side():
    """No module in forja/external may import the V2 gold/corpus machinery.

    (A bare 'manifest.json' string is fine — the NAV snapshot has its own
    manifest; what must never happen is importing or reading V2 gold.)"""
    forbidden_imports = ("goldgen", "corpusgen", "score_v2", "evaluation.gold")
    forbidden_paths = ("benchmark_data", "labels.json", "jobs_v2.json")
    for path in sorted((ROOT / "external").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                continue
            for name in names:
                for bad in forbidden_imports:
                    assert bad not in name, f"{path} imports V2 gold side: {name}"
        docs = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value not in docs:
                for bad in forbidden_paths:
                    assert bad not in node.value, \
                        f"{path} references V2 artifact path {bad!r}"
