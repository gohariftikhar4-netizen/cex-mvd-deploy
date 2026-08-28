import pytest

from forja import taxonomy


def test_cefr_ordering():
    assert taxonomy.meets_language_level("B2", "B1")
    assert taxonomy.meets_language_level("native", "C2")
    assert not taxonomy.meets_language_level("A2", "B1")
    assert not taxonomy.meets_language_level("none", "A1")


def test_cefr_unknown_level_raises():
    with pytest.raises(ValueError):
        taxonomy.cefr_rank("Z9")


def test_license_implication():
    assert taxonomy.expand_licenses(["CE"]) == {"CE", "C", "C1", "B"}
    assert taxonomy.expand_licenses(["B"]) == {"B"}
    assert "D" not in taxonomy.expand_licenses(["CE"])
    with pytest.raises(ValueError):
        taxonomy.expand_licenses(["X99"])


def test_commute_same_city_and_symmetry():
    assert taxonomy.commute_minutes("Oslo", "Oslo") == taxonomy.CITY_INTERNAL_MINUTES
    assert taxonomy.commute_minutes("Oslo", "Drammen") == taxonomy.commute_minutes("Drammen", "Oslo")


def test_commute_unlisted_pair_is_not_commutable():
    assert taxonomy.commute_minutes("Bergen", "Tromsø") == taxonomy.NOT_COMMUTABLE


def test_commute_unknown_city_raises():
    with pytest.raises(ValueError):
        taxonomy.commute_minutes("Oslo", "Narnia")


def test_transferable_paths_sorted_and_typed():
    paths = taxonomy.transfer_paths({"undervisning"}, "formidling")
    assert paths and paths[0][0] == "undervisning"
    source, weight, rationale = paths[0]
    assert 0 < weight <= 1
    assert isinstance(rationale, str) and rationale


def test_transferable_targets_are_known_skills():
    for source, targets in taxonomy.TRANSFERABLE.items():
        assert taxonomy.is_known_skill(source), source
        for target, weight, _ in targets:
            assert taxonomy.is_known_skill(target), target
            assert 0 < weight <= 1


def test_skill_alias_normalization():
    assert taxonomy.normalize_skill("K8s") == "kubernetes"
    assert taxonomy.normalize_skill("python") == "python"
    assert taxonomy.normalize_skill("underwater basket weaving") is None
