"""Parse audience facts from chat text so we don't re-ask what the user already said."""

from __future__ import annotations

import re

from app.schemas.study_brief import AGE_SEGMENTS, AudienceBrief, StudyBrief

# Inclusive bounds for each canonical Unilever segment.
_SEGMENT_BOUNDS: list[tuple[str, int, int]] = [
    ("18-24", 18, 24),
    ("25-34", 25, 34),
    ("35-44", 35, 44),
    ("45-54", 45, 54),
    ("55-64", 55, 64),
    ("65+", 65, 120),
]

_COUNTRY_ALIASES: dict[str, str] = {
    "us": "United States",
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "america": "United States",
    "india": "India",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "united kingdom": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "canada": "Canada",
    "australia": "Australia",
    "germany": "Germany",
    "france": "France",
    "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
}

_TEXT_STUDY_HINTS = (
    "positioning",
    "statement",
    "statements",
    "tagline",
    "headline",
    "copy ",
    " messaging",
    "campaign line",
    "opening",
    "openings",
    "reel should start",
    "no image",
    "don't have image",
    "dont have image",
    "without image",
    "text study",
    "text statements",
)


def segments_for_age_range(low: int, high: int) -> list[str]:
    """Canonical segments that overlap an inclusive age range."""
    if high < low:
        low, high = high, low
    low = max(18, low)
    high = min(120, high)
    out: list[str] = []
    for name, start, end in _SEGMENT_BOUNDS:
        if low <= end and high >= start:
            out.append(name)
    return out


def even_age_distribution(segments: list[str]) -> dict[str, int]:
    names = [s for s in AGE_SEGMENTS if s in segments]
    if not names:
        return {}
    base, remainder = divmod(100, len(names))
    return {
        name: base + (1 if i < remainder else 0) for i, name in enumerate(names)
    }


def infer_audience_from_text(text: str) -> dict:
    """Pull respondents, country, and age range from free text."""
    raw = text or ""
    lower = raw.lower()
    found: dict = {}

    respondents = _infer_respondents(lower)
    if respondents is not None:
        found["number_of_respondents"] = respondents

    countries = _infer_countries(lower)
    if countries:
        found["countries"] = countries

    segments = _infer_age_segments(lower)
    if segments:
        found["age_segments"] = segments
        found["age_distribution"] = even_age_distribution(segments)

    return found


def looks_like_text_study(text: str) -> bool:
    lower = (text or "").lower()
    return any(hint in lower for hint in _TEXT_STUDY_HINTS)


def apply_inferred_audience(
    brief: StudyBrief,
    *,
    text: str,
    overwrite_age_if_range: bool = True,
) -> StudyBrief:
    """Fill audience from chat text. A stated range replaces a too-narrow AI split."""
    inferred = infer_audience_from_text(text)
    if not inferred:
        return brief
    data = brief.model_copy(deep=True)
    aud = data.audience

    if inferred.get("number_of_respondents"):
        aud.number_of_respondents = inferred["number_of_respondents"]

    if inferred.get("countries"):
        aud.countries = list(inferred["countries"])

    new_segments: list[str] = inferred.get("age_segments") or []
    if new_segments:
        current = list(aud.age_segments or aud.age_distribution.keys())
        should_write = not current
        if overwrite_age_if_range and new_segments:
            # User said 25–55 but the model only filled 25–34 → replace.
            if set(new_segments) != set(current) and (
                not current or set(current).issubset(set(new_segments)) or len(new_segments) > len(current)
            ):
                should_write = True
        if should_write:
            dist = inferred.get("age_distribution") or even_age_distribution(new_segments)
            data.audience = AudienceBrief(
                number_of_respondents=aud.number_of_respondents,
                age_distribution=dist,
                countries=aud.countries,
                gender_male=aud.gender_male,
                gender_female=aud.gender_female,
            )
    return data


def apply_text_study_hint(brief: StudyBrief, *, text: str, has_images: bool) -> StudyBrief:
    if has_images or brief.study_type == "grid":
        return brief
    if brief.study_type is None and looks_like_text_study(text):
        data = brief.model_copy(deep=True)
        data.study_type = "text"
        return data
    return brief


def _infer_respondents(lower: str) -> int | None:
    patterns = (
        r"around\s+(\d{1,4})\s+respondents",
        r"about\s+(\d{1,4})\s+respondents",
        r"(\d{1,4})\s+respondents",
        r"sample\s+(?:size\s+)?(?:of\s+)?(\d{1,4})",
        r"n\s*=\s*(\d{1,4})",
        r"(\d{1,4})\s+people\b",
    )
    for pat in patterns:
        match = re.search(pat, lower)
        if match:
            n = int(match.group(1))
            if 1 <= n <= 1500:
                return n
    return None


def _infer_countries(lower: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    # Longer names first so "united states" wins over "us" inside it... we search aliases
    # as whole words / phrases.
    phrases = sorted(_COUNTRY_ALIASES.keys(), key=len, reverse=True)
    for phrase in phrases:
        if phrase in {"us", "uk"}:
            pattern = rf"(?:in the |in |from the |from |people in the )?{re.escape(phrase)}\b"
        else:
            pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, lower):
            name = _COUNTRY_ALIASES[phrase]
            if name.lower() not in seen:
                found.append(name)
                seen.add(name.lower())
    return found


def _infer_age_segments(lower: str) -> list[str]:
    # "25–55", "25-55", "25 to 55", "ages 25-55", "18-34 year olds"
    match = re.search(
        r"(?:age(?:s|d)?|target age|year[\s-]*olds?)[^\d]{0,12}(\d{2})\s*(?:-|–|to|through)\s*(\d{2})",
        lower,
    )
    if not match:
        match = re.search(
            r"(\d{2})\s*(?:-|–|to)\s*(\d{2})\s*(?:year[\s-]*olds?|years?\s+old)",
            lower,
        )
    if not match:
        match = re.search(r"\b(\d{2})\s*(?:-|–)\s*(\d{2})\b", lower)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if 15 <= low <= 90 and 15 <= high <= 100 and abs(high - low) >= 2:
            return segments_for_age_range(low, high)

    named: list[str] = []
    for seg in AGE_SEGMENTS:
        if seg.lower() in lower:
            named.append(seg)
    return named
