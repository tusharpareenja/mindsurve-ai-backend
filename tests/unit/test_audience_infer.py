"""Audience inference from free-text study requests."""

from app.schemas.study_brief import AudienceBrief, StudyBrief
from app.services.audience_infer import (
    apply_inferred_audience,
    infer_audience_from_text,
    looks_like_text_study,
    segments_for_age_range,
)


def test_age_range_25_55_maps_to_overlapping_segments() -> None:
    segs = segments_for_age_range(25, 55)
    assert segs == ["25-34", "35-44", "45-54", "55-64"]


def test_infer_full_smb_prompt() -> None:
    text = (
        "We're launching an AI tool for small business owners that automatically "
        "creates social media content. I want to find out what positioning would "
        "make business owners most interested. The study should focus on people "
        "in the US who own or manage a small business. Target age 25–55 and "
        "around 10 respondents."
    )
    found = infer_audience_from_text(text)
    assert found["number_of_respondents"] == 10
    assert found["countries"] == ["United States"]
    assert found["age_segments"] == ["25-34", "35-44", "45-54", "55-64"]
    assert sum(found["age_distribution"].values()) == 100
    assert looks_like_text_study(text)


def test_apply_inferred_replaces_too_narrow_age() -> None:
    brief = StudyBrief(
        audience=AudienceBrief(
            number_of_respondents=10,
            age_distribution={"25-34": 100},
            countries=["United States"],
        )
    )
    result = apply_inferred_audience(
        brief,
        text="Target age 25-55 in the US with 10 respondents",
    )
    assert set(result.audience.age_distribution) == {
        "25-34",
        "35-44",
        "45-54",
        "55-64",
    }
    assert sum(result.audience.age_distribution.values()) == 100
    assert result.audience.number_of_respondents == 10
    assert result.audience.countries == ["United States"]


def test_18_34_year_olds() -> None:
    found = infer_audience_from_text("test among 18–34 year olds in India with 20 respondents")
    assert found["number_of_respondents"] == 20
    assert found["countries"] == ["India"]
    assert found["age_segments"] == ["18-24", "25-34"]
    assert found["age_distribution"] == {"18-24": 50, "25-34": 50}
