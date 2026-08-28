"""Every hard-constraint dimension: one passing and one violating case."""

from forja.pipeline import constraints


def _dims(report):
    return {v.dimension for v in report.violations}


def test_clean_pair_passes(make_candidate, make_job):
    report = constraints.check(make_candidate(), make_job())
    assert report.passed
    assert report.unverified == ()


def test_citizenship_requirement(make_candidate, make_job):
    job = make_job(requirements={"requires_norwegian_citizenship": True})
    assert constraints.check(make_candidate(), job).passed  # citizen
    report = constraints.check(make_candidate(work_authorization="permanent_permit"), job)
    assert _dims(report) == {"work_authorization"}


def test_commute_violation_and_remote_exemption(make_candidate, make_job):
    cand = make_candidate()  # Oslo, max 45 min
    far = make_job(location_city="Trondheim")
    assert _dims(constraints.check(cand, far)) == {"location_commute"}
    remote = make_job(location_city="Trondheim", work_mode="remote")
    assert constraints.check(cand, remote).passed


def test_relocation_allows_far_job(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"willing_to_relocate": True, "relocation_counties": []})
    far = make_job(location_city="Trondheim")
    assert constraints.check(cand, far).passed
    picky = make_candidate(hard_constraints={"willing_to_relocate": True, "relocation_counties": ["Vestland"]})
    assert _dims(constraints.check(picky, far)) == {"location_commute"}


def test_commute_boundary_is_inclusive(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"max_commute_minutes": 40})
    job = make_job(location_city="Drammen")  # Oslo–Drammen = 40
    assert constraints.check(cand, job).passed


def test_shift_violation(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"cannot_work_shifts": ["night"]})
    assert constraints.check(cand, make_job(shifts=["day", "evening"])).passed
    report = constraints.check(cand, make_job(shifts=["day", "night"]))
    assert _dims(report) == {"shifts"}


def test_physical_violation(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"physical_limitations": ["no_heavy_lifting"]})
    assert constraints.check(cand, make_job()).passed
    report = constraints.check(cand, make_job(requirements={"physical_demands": ["heavy_lifting"]}))
    assert _dims(report) == {"physical"}
    # A different demand is not blocked by this limitation.
    other = make_job(requirements={"physical_demands": ["working_at_heights"]})
    assert constraints.check(cand, other).passed


def test_driving_license_with_implication(make_candidate, make_job):
    job_c = make_job(requirements={"driving_license_required": "C"})
    assert _dims(constraints.check(make_candidate(), job_c)) == {"driving_license"}  # has B only
    ce_holder = make_candidate(driving_licenses=["CE"])
    assert constraints.check(ce_holder, job_c).passed


def test_certification_requirement(make_candidate, make_job):
    job = make_job(requirements={"certifications_required": ["fagbrev_elektriker"]})
    assert _dims(constraints.check(make_candidate(), job)) == {"certifications"}
    holder = make_candidate(certifications=["fagbrev_elektriker"])
    assert constraints.check(holder, job).passed


def test_language_requirements(make_candidate, make_job):
    cand = make_candidate(languages={"norwegian": "B1", "english": "C1"})
    assert constraints.check(cand, make_job(requirements={"norwegian_min_level": "B1"})).passed
    report = constraints.check(cand, make_job(requirements={"norwegian_min_level": "C1"}))
    assert _dims(report) == {"language_norwegian"}
    # Missing language counts as "none".
    no_english = make_candidate(languages={"norwegian": "native"})
    report = constraints.check(no_english, make_job(requirements={"english_min_level": "B1"}))
    assert _dims(report) == {"language_english"}


def test_percent_position(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"percent_min": 60, "percent_max": 80})
    assert constraints.check(cand, make_job(percent_position=80)).passed
    assert _dims(constraints.check(cand, make_job(percent_position=100))) == {"percent_position"}
    assert _dims(constraints.check(cand, make_job(percent_position=40))) == {"percent_position"}


def test_salary_floor_and_unverified(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"min_salary_nok": 600000})
    below = make_job(salary_nok_min=450000, salary_nok_max=550000)
    assert _dims(constraints.check(cand, below)) == {"salary"}
    at_floor = make_job(salary_nok_min=550000, salary_nok_max=600000)
    assert constraints.check(cand, at_floor).passed
    undisclosed = make_job()
    report = constraints.check(cand, undisclosed)
    assert report.passed and report.unverified == ("salary",)
    # No floor -> nothing to verify.
    assert constraints.check(make_candidate(), undisclosed).unverified == ()


def test_overnight_travel(make_candidate, make_job):
    cand = make_candidate(hard_constraints={"no_overnight_travel": True})
    assert constraints.check(cand, make_job()).passed
    report = constraints.check(cand, make_job(requires_overnight_travel=True))
    assert _dims(report) == {"overnight_travel"}


def test_violation_evidence_is_machine_checkable(make_candidate, make_job):
    report = constraints.check(
        make_candidate(hard_constraints={"cannot_work_shifts": ["night"]}),
        make_job(shifts=["night"]),
    )
    v = report.violations[0]
    assert v.candidate_ref and v.job_ref and v.reason
    assert "night" in v.job_value


def test_filter_eligible_splits_and_reports(make_candidate, make_job):
    cand = make_candidate()
    good = make_job(id="job_901")
    bad = make_job(id="job_902", location_city="Bergen")
    eligible, reports = constraints.filter_eligible(cand, [good, bad])
    assert [j.id for j in eligible] == ["job_901"]
    assert len(reports) == 2
    assert not [r for r in reports if r.job_id == "job_902"][0].passed
