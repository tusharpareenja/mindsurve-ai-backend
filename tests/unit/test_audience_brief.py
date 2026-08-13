"""Audience normalization and percentage behavior."""

from app.schemas.study_brief import AudienceBrief


def test_exact_age_maps_to_canonical_segment() -> None:
    audience = AudienceBrief(age_segments=["23"])

    assert audience.age_segments == ["18-24"]
    assert audience.age_distribution == {"18-24": 100}


def test_multiple_ages_in_same_segment_are_deduplicated() -> None:
    audience = AudienceBrief(age_segments=["55", "56"])

    assert audience.age_segments == ["55-64"]
    assert audience.age_distribution == {"55-64": 100}


def test_selected_segments_get_even_distribution() -> None:
    audience = AudienceBrief(age_segments=["18-24", "25-34", "35-44"])

    assert sum(audience.age_distribution.values()) == 100
    assert audience.age_distribution == {
        "18-24": 34,
        "25-34": 33,
        "35-44": 33,
    }


def test_explicit_distribution_is_preserved() -> None:
    audience = AudienceBrief(
        age_distribution={"18 - 24": 60, "25-34": 40},
        gender_male=60,
        gender_female=40,
    )

    assert audience.age_distribution == {"18-24": 60, "25-34": 40}
    assert audience.gender_male == 60
    assert audience.gender_female == 40
