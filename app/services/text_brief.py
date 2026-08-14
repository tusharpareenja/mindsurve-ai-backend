"""Guarantee a valid text-study category/statement structure after AI turns.

Statements are STIMULI — the copy respondents rate in tasks. They must never
include the study title, platform names, or researcher jargon.
"""

from __future__ import annotations

import re

from app.schemas.study_brief import (
    MAX_STATEMENT_CHARS,
    MAX_TEXT_CATEGORIES,
    MAX_TEXT_STATEMENTS,
    MIN_TEXT_CATEGORIES,
    MIN_TEXT_STATEMENTS,
    TEXT_GENERATE_CATEGORIES,
    TEXT_GENERATE_STATEMENTS,
    CategoryBrief,
    ElementBrief,
    StudyBrief,
)

_META_PHRASES = (
    "instagram reel",
    "instagram reels",
    "this study",
    "this hook",
    "this video",
    "stop scrolling — testing",
    "openings for",
    "study brief",
)


def _clip(text: str) -> str:
    return (text or "").strip()[:MAX_STATEMENT_CHARS]


def _as_statement(text: str) -> ElementBrief:
    statement = _clip(text)
    return ElementBrief(
        name=statement,
        element_type="text",
        content=statement,
    )


def _blob(brief: StudyBrief) -> str:
    return " ".join(
        part
        for part in (
            brief.title,
            brief.background,
            brief.main_question,
            brief.orientation_text,
        )
        if part
    ).lower()


def _is_meta_statement(text: str, title: str) -> bool:
    """True if a line talks about the study instead of being stimulus copy."""
    raw = (text or "").strip().lower()
    if not raw:
        return True
    title_key = (title or "").strip().lower()
    if title_key and len(title_key) >= 12 and title_key in raw:
        return True
    return any(phrase in raw for phrase in _META_PHRASES)


def _domain(brief: StudyBrief) -> str:
    text = _blob(brief)
    if re.search(
        r"\b(weight\s*loss|lose\s*weight|fitness|workout|gym|diet|calorie)\b",
        text,
    ):
        return "weight_loss"
    if re.search(r"\b(god|faith|worship|spiritual|church|prayer)\b", text):
        return "belief"
    if re.search(r"\b(skincare|serum|beauty|skin\b|makeup)\b", text):
        return "beauty"
    return "generic"


def _theme_bank(domain: str) -> list[tuple[str, list[str]]]:
    """Ready-to-rate stimulus lines. Never interpolate the study title."""
    if domain == "weight_loss":
        return [
            (
                "Bold claim",
                [
                    "Lose weight without giving up your favorite foods",
                    "Cardio isn't what's keeping the weight on",
                    "You don't need a 5am club to get lean",
                    "I stopped counting calories and still lost weight",
                    "The gym isn't the missing piece — your evenings are",
                    "Forget another Monday restart. This actually sticks",
                ],
            ),
            (
                "Relatable struggle",
                [
                    "Tired of starting a diet every Monday?",
                    "I was eating 'clean' and still not losing weight",
                    "That 9pm snack undoing the whole day — sound familiar?",
                    "Workout done. Scale didn't move. Again.",
                    "If weight loss advice worked, you wouldn't still be stuck",
                    "Busy all day, then the late-night hunger hits",
                ],
            ),
            (
                "Question hook",
                [
                    "What if cardio isn't the reason the weight isn't dropping?",
                    "Are you still guessing your way through weight loss?",
                    "Why does it work for them but not for you?",
                    "Would you try this if it only took 10 minutes?",
                    "Is your current plan actually the problem?",
                    "How many times have you restarted this year?",
                ],
            ),
            (
                "Social proof",
                [
                    "The 7-day challenge people actually finish",
                    "She tried this for 30 days — no gym required",
                    "Join the weight-loss challenge everyone keeps sharing",
                    "Real kitchens. Real weeks. Real results.",
                    "This is the opening 20k people saved",
                    "Friends keep sending this one around",
                ],
            ),
            (
                "Transformation",
                [
                    "From 'I'll start Monday' to consistent in 30 days",
                    "The shift that made mornings feel lighter",
                    "Small wins that actually showed up on the scale",
                    "I didn't overhaul my life — I changed one hour",
                    "A calmer way to start this week",
                    "Watch what happens when the plan fits real life",
                ],
            ),
        ]
    if domain == "belief":
        return [
            (
                "Naturalist",
                [
                    "I feel closest to God out in nature",
                    "Too much time indoors leaves me cold",
                    "The words creation, wilderness, and beauty speak to me",
                    "I'd happily spend hours walking a quiet trail",
                    "I'd rather meet God outdoors than indoors",
                    "A still lake does more for me than a crowded room",
                ],
            ),
            (
                "Sensate",
                [
                    "I feel closest to God in art and music",
                    "Plain worship with no beauty leaves me cold",
                    "The words sight, sound, and splendour speak to me",
                    "I'd happily spend hours in a beautiful sanctuary",
                    "I'd rather worship somewhere beautiful than somewhere plain",
                    "A single note can feel like prayer",
                ],
            ),
            (
                "Traditionalist",
                [
                    "The words tradition, ritual, and history speak to me",
                    "I'd happily spend hours following a set liturgy",
                    "I'd rather keep tradition than make it up",
                    "I feel closest to God in familiar ritual",
                    "Worship with no history leaves me cold",
                    "The old prayers still fit when I don't have words",
                ],
            ),
            (
                "Ascetic",
                [
                    "I feel closest to God alone and quiet",
                    "Constant noise and crowds leave me cold",
                    "The words silence, solitude, and discipline speak to me",
                    "I'd happily spend hours alone, fasting and praying",
                    "I'd rather pray alone than worship in crowds",
                    "Quiet is not empty — it's where I hear clearly",
                ],
            ),
        ]
    if domain == "beauty":
        return [
            (
                "Bold claim",
                [
                    "This undoes a week of late nights in one night",
                    "You don't need a 12-step routine to see a change",
                    "I stopped layering products and my skin calmed down",
                    "Glow without the 6am ritual",
                    "The shortcut your bathroom shelf has been missing",
                    "Forget the 10-step routine — this is the one step",
                ],
            ),
            (
                "Relatable struggle",
                [
                    "Tired of buying another serum that does nothing?",
                    "My skin looks tired even when I'm not",
                    "I did everything 'right' and still woke up dull",
                    "The 9pm scroll is written all over my face",
                    "If more products worked, your shelf wouldn't be this full",
                    "Good lighting hides it. Morning light doesn't.",
                ],
            ),
            (
                "Question hook",
                [
                    "What if your routine is the reason nothing changes?",
                    "Are you still guessing which product actually works?",
                    "Would you try this if it took under a minute?",
                    "Is your skin tired — or just over-treated?",
                    "How many serums have you quit this year?",
                    "Why does it work in the ad but not on you?",
                ],
            ),
            (
                "Social proof",
                [
                    "The night routine people actually finish",
                    "She swapped her shelf for this one step",
                    "Saved 40k times — not because of the lighting",
                    "Real bathrooms. Real mornings. Real skin.",
                    "Friends keep asking what changed",
                    "This is the bottle that doesn't get abandoned",
                ],
            ),
        ]
    return [
        (
            "Bold claim",
            [
                "You don't need to overhaul your life for this to work",
                "I stopped doing it the hard way and still got results",
                "Forget what you were told this had to look like",
                "The shortcut nobody puts in the caption",
                "This takes less time than your morning scroll",
                "One change. Not a whole new personality.",
            ],
        ),
        (
            "Relatable struggle",
            [
                "Tired of starting over every Monday?",
                "I was doing everything 'right' and still stuck",
                "That late-night feeling that none of this is working",
                "If the advice worked, you wouldn't still be here",
                "Busy all day — then the plan falls apart at night",
                "It sounds easy until you try it on a real week",
            ],
        ),
        (
            "Question hook",
            [
                "What if this didn't have to be so hard?",
                "Are you still guessing your way through it?",
                "Why does it work for them but not for you?",
                "Would you try this if it only took 10 minutes?",
                "Is your current plan actually the problem?",
                "How many times have you restarted this year?",
            ],
        ),
        (
            "Social proof",
            [
                "The version people actually finish",
                "She tried this for 30 days and didn't quit",
                "Friends keep sending this one around",
                "Real life. Not a studio. Still works.",
                "This is the line people save and come back to",
                "Join the ones who stopped starting over",
            ],
        ),
    ]


def _existing_texts(brief: StudyBrief) -> set[str]:
    seen: set[str] = set()
    for cat in brief.categories:
        for el in cat.elements:
            key = (el.content or el.name).strip().lower()
            if key:
                seen.add(key)
    return seen


def _strip_meta_statements(brief: StudyBrief) -> StudyBrief:
    title = brief.title or ""
    for cat in brief.categories:
        cat.elements = [
            el
            for el in cat.elements
            if not _is_meta_statement(el.content or el.name, title)
        ]
    return brief


def _pad_category(
    cat: CategoryBrief,
    *,
    extras: list[str],
    seen: set[str],
    target: int,
    title: str,
) -> CategoryBrief:
    elements = list(cat.elements)
    for line in extras:
        if len(elements) >= target:
            break
        if _is_meta_statement(line, title):
            continue
        statement = _clip(line)
        key = statement.lower()
        if not statement or key in seen:
            continue
        elements.append(_as_statement(statement))
        seen.add(key)
    cat.elements = elements[:MAX_TEXT_STATEMENTS]
    return cat


def _category_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def dedupe_similar_categories(brief: StudyBrief) -> StudyBrief:
    """Merge categories that only differ by hyphen / spacing / case."""
    data = brief.model_copy(deep=True)
    merged: list[CategoryBrief] = []
    index: dict[str, int] = {}
    for cat in data.categories:
        key = _category_key(cat.name)
        if not key:
            merged.append(cat)
            continue
        if key in index:
            existing = merged[index[key]]
            seen = {
                (el.content or el.name).strip().lower() for el in existing.elements
            }
            for el in cat.elements:
                line = (el.content or el.name).strip().lower()
                if line and line not in seen:
                    existing.elements.append(el)
                    seen.add(line)
            if " " in cat.name and "-" in existing.name:
                existing.name = cat.name
            continue
        index[key] = len(merged)
        merged.append(cat)
    data.categories = merged
    return data


def ensure_text_study_structure(
    brief: StudyBrief,
    *,
    target_categories: int | None = None,
) -> StudyBrief:
    """Keep a valid 3+ category pack without inventing meta / title-based copy.

    - Drop statements that quote the study title or talk about the research.
    - Don't inject filler into a category that already has ≥3 real statements.
    - Add themed categories of actual stimulus lines until the floor is met.
    """
    if brief.study_type != "text":
        return brief

    data = brief.model_copy(deep=True)
    data = dedupe_similar_categories(data)
    data = _strip_meta_statements(data)
    bank = _theme_bank(_domain(data))
    seen = _existing_texts(data)
    title = data.title or ""

    padded: list[CategoryBrief] = []
    used_keys = {_category_key(c.name) for c in data.categories if _category_key(c.name)}
    bank_idx = 0

    for cat in data.categories:
        current = len(cat.elements)
        if current >= MIN_TEXT_STATEMENTS:
            # Keep the user's / AI's real copy. Do not mix in template lines.
            cat.elements = cat.elements[:MAX_TEXT_STATEMENTS]
            padded.append(cat)
            continue
        extras: list[str] = []
        if bank_idx < len(bank):
            extras = bank[bank_idx][1]
            bank_idx += 1
        padded.append(
            _pad_category(
                cat,
                extras=extras,
                seen=seen,
                target=MIN_TEXT_STATEMENTS,
                title=title,
            )
        )

    padded = [c for c in padded if c.elements]
    used_keys = {_category_key(c.name) for c in padded if _category_key(c.name)}

    if target_categories is not None:
        target_cats = max(MIN_TEXT_CATEGORIES, min(MAX_TEXT_CATEGORIES, target_categories))
    else:
        target_cats = (
            TEXT_GENERATE_CATEGORIES
            if len(padded) < MIN_TEXT_CATEGORIES
            else len(padded)
        )

    for name, lines in bank:
        if len(padded) >= target_cats or len(padded) >= MAX_TEXT_CATEGORIES:
            break
        if _category_key(name) in used_keys:
            continue
        cat = CategoryBrief(name=name[:100], elements=[])
        cat = _pad_category(
            cat,
            extras=lines,
            seen=seen,
            target=TEXT_GENERATE_STATEMENTS,
            title=title,
        )
        if len(cat.elements) < MIN_TEXT_STATEMENTS:
            continue
        padded.append(cat)
        used_keys.add(_category_key(name))

    n = 1
    generic = _theme_bank("generic")
    generic_lines = [line for _, lines in generic for line in lines]
    while len(padded) < target_cats:
        name = f"Theme {n}"
        n += 1
        if _category_key(name) in used_keys:
            continue
        cat = CategoryBrief(name=name, elements=[])
        cat = _pad_category(
            cat,
            extras=generic_lines,
            seen=seen,
            target=TEXT_GENERATE_STATEMENTS,
            title=title,
        )
        if len(cat.elements) < MIN_TEXT_STATEMENTS:
            break
        padded.append(cat)
        used_keys.add(_category_key(name))

    data.categories = padded[:MAX_TEXT_CATEGORIES]
    return data
