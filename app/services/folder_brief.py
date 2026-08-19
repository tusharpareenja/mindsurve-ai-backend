"""Apply user-provided folder → category image structure onto a study brief."""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable

from app.schemas.study_brief import (
    DEFAULT_LAYER_TRANSFORM,
    AttachmentBrief,
    CategoryBrief,
    ClassificationQuestionBrief,
    ElementBrief,
    LayerBrief,
    LayerElementBrief,
    LayerTransformBrief,
    StudyBrief,
)
from app.services.synthetic_capacity import min_classification_question_count

_EXT = re.compile(r"\.[^.]+$")
_PREFIX_DELIM = re.compile(r"[._\-\s]")


def _is_image_attachment(att: AttachmentBrief) -> bool:
    ctype = (att.content_type or "").lower()
    if ctype.startswith("image/"):
        return True
    name = (att.filename or att.relative_path or att.url or "").lower()
    return bool(re.search(r"\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)", name))


def element_name_from_filename(filename: str) -> str:
    base = _EXT.sub("", filename.strip())
    return (base or filename).strip()[:150]


def _prefix_split(
    items: list[AttachmentBrief],
) -> "OrderedDict[str, list[AttachmentBrief]] | None":
    """Group images by a shared filename prefix (e.g. ``Aura.Shape1`` → group ``Aura``).

    Returns an ordered mapping of ``prefix -> [attachment]`` only when the split is
    clean (every file has a prefix, 2–15 groups, ≥2 files per group). Otherwise
    returns ``None`` so the caller keeps the original structure. Element names are
    kept as the FULL filename base (``Aura.Shape1``) so they stay unique.
    """
    groups: OrderedDict[str, list[AttachmentBrief]] = OrderedDict()
    for att in items:
        base = element_name_from_filename(att.filename or "")
        match = _PREFIX_DELIM.search(base)
        if not match or match.start() == 0:
            return None
        prefix = base[: match.start()].strip()
        if not prefix:
            return None
        groups.setdefault(prefix[:100], []).append(att)

    if not (2 <= len(groups) <= 15):
        return None
    if any(len(items) < 2 for items in groups.values()):
        return None
    return groups


def _categories_from_prefix_split(
    groups: "OrderedDict[str, list[AttachmentBrief]]",
) -> list[CategoryBrief]:
    categories: list[CategoryBrief] = []
    for cat_name, items in groups.items():
        elements = [
            ElementBrief(
                name=element_name_from_filename(att.filename or "image"),
                element_type="image",
                content=att.url,
            )
            for att in items
        ]
        categories.append(CategoryBrief(name=cat_name, elements=elements))
    return categories


def _ensure_unique_element_names(brief: StudyBrief) -> StudyBrief:
    """Guarantee element names are unique across the whole study."""
    seen: dict[str, int] = {}
    for cat in brief.categories:
        for el in cat.elements:
            base = (el.name or "").strip() or "Element"
            key = base.lower()
            if key in seen:
                seen[key] += 1
                suffix = f" ({seen[key]})"
                el.name = base[: 150 - len(suffix)] + suffix
            else:
                seen[key] = 1
    for layer in brief.layers:
        for el in layer.elements:
            base = (el.name or "").strip() or "Element"
            key = base.lower()
            if key in seen:
                seen[key] += 1
                suffix = f" ({seen[key]})"
                el.name = base[: 150 - len(suffix)] + suffix
            else:
                seen[key] = 1
    return brief


def _default_transform() -> LayerTransformBrief:
    return LayerTransformBrief(**DEFAULT_LAYER_TRANSFORM)


def looks_like_layer_folder_upload(attachments: list[AttachmentBrief]) -> bool:
    """Root background image(s) + at least one categorized layer folder."""
    images = [a for a in attachments if a.url and _is_image_attachment(a)]
    has_background = any(a.is_background for a in images)
    has_layers = any((a.category or "").strip() and not a.is_background for a in images)
    return has_background and has_layers


def apply_folder_layers(
    brief: StudyBrief, attachments: list[AttachmentBrief]
) -> StudyBrief:
    """Map root-folder uploads onto a layer study.

    - ``is_background`` attachments → ``background_image_url``
    - categorized attachments → layers ordered by ``layer_order`` (then name)
    - default transform ``0/0/100/100`` on every layer and element
    """
    data = brief.model_copy(deep=True)
    images = [a for a in attachments if a.url and _is_image_attachment(a)]
    backgrounds = [a for a in images if a.is_background]
    layer_items = [
        a for a in images if not a.is_background and (a.category or "").strip()
    ]
    if not layer_items:
        return data

    if backgrounds:
        data.background_image_url = backgrounds[0].url

    buckets: OrderedDict[str, list[AttachmentBrief]] = OrderedDict()
    # Prefer explicit layer_order when present.
    ordered_names = sorted(
        {
            (a.category or "").strip()[:100]
            for a in layer_items
            if (a.category or "").strip()
        },
        key=lambda name: (
            min(
                (
                    a.layer_order
                    for a in layer_items
                    if (a.category or "").strip()[:100] == name
                    and isinstance(a.layer_order, int)
                ),
                default=10_000,
            ),
            name.lower(),
        ),
    )
    for name in ordered_names:
        buckets[name] = [
            a for a in layer_items if (a.category or "").strip()[:100] == name
        ]

    layers: list[LayerBrief] = []
    for z_index, (name, items) in enumerate(buckets.items()):
        elements: list[LayerElementBrief] = []
        for img_i, att in enumerate(items):
            elements.append(
                LayerElementBrief(
                    name=element_name_from_filename(att.filename or "image"),
                    content=att.url,
                    order=img_i,
                    transform=_default_transform(),
                )
            )
        layers.append(
            LayerBrief(
                name=name,
                z_index=z_index,
                order=z_index,
                elements=elements,
                transform=_default_transform(),
            )
        )

    data.study_type = "layer"
    data.layers = layers
    # Layer studies don't use grid categories for stimuli.
    data.categories = []
    return _ensure_unique_element_names(data)


def apply_folder_categories(
    brief: StudyBrief, attachments: list[AttachmentBrief]
) -> StudyBrief:
    """Turn uploaded images into grid categories/elements or layer packs.

    Priority:
    0. Layer pack (root background + layer folders) → ``apply_folder_layers``.
    1. Explicit folder categories (subfolders) become categories.
    2. If everything collapsed into a single folder, try to derive sub-categories
       from a shared filename prefix (``Aura.Shape1`` → category ``Aura``).
    3. Flat images (no folders) are grouped by filename prefix when possible,
       else mapped onto existing elements by name, else a single ``Images`` category.

    Non-image uploads (PDF / Word / text) are ignored here — they feed the AI
    as document context instead of becoming grid elements.
    """
    if looks_like_layer_folder_upload(attachments) or (
        brief.study_type == "layer"
        and any(
            a.url and _is_image_attachment(a) and (a.category or a.is_background)
            for a in attachments
        )
    ):
        return apply_folder_layers(brief, attachments)

    data = brief.model_copy(deep=True)
    with_url = [a for a in attachments if a.url and _is_image_attachment(a)]
    categorized = [a for a in with_url if a.category and not a.is_background]

    if categorized:
        buckets: OrderedDict[str, list[AttachmentBrief]] = OrderedDict()
        for item in categorized:
            cat = (item.category or "Category").strip()[:100] or "Category"
            buckets.setdefault(cat, []).append(item)

        # A single folder with structured filenames → split into sub-categories.
        if len(buckets) == 1:
            only_items = next(iter(buckets.values()))
            split = _prefix_split(only_items)
            if split:
                data.study_type = "grid"
                data.categories = _categories_from_prefix_split(split)
                return _ensure_unique_element_names(data)

        data.study_type = "grid"
        data.categories = []
        for cat_name, items in buckets.items():
            elements = [
                ElementBrief(
                    name=element_name_from_filename(att.filename or "image"),
                    element_type="image",
                    content=att.url,
                    description="",
                )
                for att in items
            ]
            data.categories.append(CategoryBrief(name=cat_name, elements=elements))
        return _ensure_unique_element_names(data)

    # No folder categories — flat images.
    flat = [a for a in with_url if not a.category and not a.is_background]
    if not flat:
        return data

    if data.study_type in (None, "grid"):
        split = _prefix_split(flat)
        if split:
            data.study_type = "grid"
            data.categories = _categories_from_prefix_split(split)
            return _ensure_unique_element_names(data)

        # Map onto existing named elements by filename when we can.
        by_name = {
            element_name_from_filename(a.filename).lower(): a
            for a in flat
            if a.filename
        }
        mapped_any = False
        for cat in data.categories:
            for el in cat.elements:
                key = element_name_from_filename(el.name).lower()
                match = by_name.get(key) or by_name.get(el.name.lower())
                if match and not el.content.strip():
                    el.element_type = "image"
                    el.content = match.url
                    el.name = element_name_from_filename(match.filename)
                    mapped_any = True

        if not mapped_any and not data.categories:
            data.study_type = "grid"
            data.categories = [
                CategoryBrief(
                    name="Images",
                    elements=[
                        ElementBrief(
                            name=element_name_from_filename(a.filename or "image"),
                            element_type="image",
                            content=a.url,
                        )
                        for a in flat
                    ],
                )
            ]
    return _ensure_unique_element_names(data)


def _default_classification_pool() -> list[ClassificationQuestionBrief]:
    """Binary fallback screeners; extend generically when N needs >5 questions."""
    return _classification_pool_for_domain("generic")


def _is_generic_cpg_screener(text: str) -> bool:
    lower = (text or "").strip().lower()
    markers = (
        "this product category",
        "this category",
        "packaging or visual design",
        "packaging",
        "visual design when shopping",
        "new brand in this category",
        "design or brand trends",
        "premium options in this category",
        "brand reputation important",
        "switch brands in this category",
        "better design in this category",
        "advertising for this category",
        "recommended a product in this category",
        "research products online before buying in this category",
        "purchased a product in this category",
        "do you agree with statement",
    )
    return any(m in lower for m in markers)


def _classification_domain(brief: StudyBrief) -> str:
    blob = " ".join(
        part
        for part in (
            brief.title,
            brief.background,
            brief.main_question,
            brief.orientation_text,
            brief.study_type or "",
        )
        if part
    ).lower()
    if re.search(r"\b(packaging|logo|visual design|grid)\b", blob) and brief.study_type == "grid":
        return "visual"
    if re.search(
        r"\b(small business|smb|social media|ai tool|positioning|content creation)\b",
        blob,
    ):
        return "smb_ai"
    if re.search(r"\b(weight\s*loss|fitness|workout|diet|gym)\b", blob):
        return "fitness"
    return "generic"


def _classification_pool_for_domain(domain: str) -> list[ClassificationQuestionBrief]:
    if domain == "smb_ai":
        return [
            ClassificationQuestionBrief(
                question_text="Do you own or manage a small business?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="How often do you post on social media for your business?",
                options=[
                    "Daily",
                    "A few times a week",
                    "Weekly",
                    "Rarely",
                    "Never",
                ],
            ),
            ClassificationQuestionBrief(
                question_text="Who currently creates your business social media content?",
                options=[
                    "I do it myself",
                    "An employee",
                    "An agency or freelancer",
                    "Nobody regularly",
                ],
            ),
            ClassificationQuestionBrief(
                question_text="Have you used an AI tool for marketing or content creation?",
                options=["Yes, regularly", "Yes, once or twice", "No, but I'm curious", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="How important is saving time on social media posts?",
                options=[
                    "Not important",
                    "Somewhat important",
                    "Very important",
                    "Essential",
                ],
            ),
            ClassificationQuestionBrief(
                question_text="How confident are you creating social posts without a designer?",
                options=["Not confident", "Somewhat confident", "Very confident"],
            ),
            ClassificationQuestionBrief(
                question_text="Would you try a new AI tool if it drafted a week of posts for you?",
                options=["Yes", "Maybe", "No"],
            ),
        ]
    if domain == "fitness":
        return [
            ClassificationQuestionBrief(
                question_text="Are you currently trying to lose weight or get fitter?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="How often do you watch fitness content on social media?",
                options=["Daily", "A few times a week", "Weekly", "Rarely", "Never"],
            ),
            ClassificationQuestionBrief(
                question_text="Have you followed a diet or workout plan in the last 6 months?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="How do you prefer to get fitness advice?",
                options=["Short videos", "Longer videos", "Articles", "A coach or trainer"],
            ),
            ClassificationQuestionBrief(
                question_text="How important is seeing results quickly?",
                options=["Not important", "Somewhat important", "Very important"],
            ),
            ClassificationQuestionBrief(
                question_text="Do you currently pay for a fitness app or program?",
                options=["Yes", "No"],
            ),
        ]
    if domain == "visual":
        return [
            ClassificationQuestionBrief(
                question_text="How familiar are you with this product category?",
                options=["Familiar", "Not familiar"],
            ),
            ClassificationQuestionBrief(
                question_text="Have you purchased a product in this category in the last 6 months?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="Do you usually notice packaging or visual design when shopping?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="Would you consider trying a new brand in this category?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="Do you follow design or brand trends related to this category?",
                options=["Yes", "No"],
            ),
            ClassificationQuestionBrief(
                question_text="Would you pay more for better design in this category?",
                options=["Yes", "No"],
            ),
        ]
    return [
        ClassificationQuestionBrief(
            question_text="How often do you come across this kind of product or idea?",
            options=["Daily", "Weekly", "Monthly", "Rarely", "Never"],
        ),
        ClassificationQuestionBrief(
            question_text="Have you tried something like this in the last 6 months?",
            options=["Yes", "No"],
        ),
        ClassificationQuestionBrief(
            question_text="How open are you to trying a new option in this area?",
            options=["Not open", "Somewhat open", "Very open"],
        ),
        ClassificationQuestionBrief(
            question_text="Who usually decides what you use or buy here?",
            options=["Me", "Someone else", "We decide together"],
        ),
        ClassificationQuestionBrief(
            question_text="How important is this topic in your daily life?",
            options=["Not important", "Somewhat important", "Very important"],
        ),
        ClassificationQuestionBrief(
            question_text="Would you recommend a good option in this area to a friend?",
            options=["Yes", "Maybe", "No"],
        ),
        ClassificationQuestionBrief(
            question_text="Do you research options before choosing?",
            options=["Always", "Sometimes", "Rarely", "Never"],
        ),
    ]


def ensure_default_classification(
    brief: StudyBrief,
    *,
    avoid_texts: Iterable[str] | None = None,
    min_count: int | None = None,
) -> StudyBrief:
    """Guarantee enough screeners for the requested sample size.

    Target count = max(5, ceil(log2(N))) so capacity can cover N even at 2 options
    each (e.g. 200 → 8, 300 → 9). Uses domain-specific questions (SMB/AI, fitness,
    visual) instead of generic packaging copy when that doesn't fit the study.

    ``min_count`` raises the floor when the user asked for more screeners
    (e.g. "add 5 more" / "at least 15 screening questions").

    ``avoid_texts`` holds questions the user just deleted, so backfilling never
    restores the exact question they asked to remove.
    """
    data = brief.model_copy(deep=True)
    capacity_floor = min_classification_question_count(
        data.audience.number_of_respondents
    )
    target = capacity_floor
    if isinstance(min_count, int) and min_count > 0:
        target = max(target, min(min_count, 30))
    domain = _classification_domain(data)
    defaults = _classification_pool_for_domain(domain)
    blocked = {text.strip().lower() for text in (avoid_texts or []) if text.strip()}

    existing = list(data.classification_questions)
    if domain != "visual":
        existing = [q for q in existing if not _is_generic_cpg_screener(q.question_text)]

    if len(existing) >= target:
        data.classification_questions = existing
        return data

    existing_texts = {q.question_text.strip().lower() for q in existing}
    padded = list(existing)
    for item in defaults:
        if len(padded) >= target:
            break
        text = item.question_text.strip().lower()
        if text in existing_texts or text in blocked:
            continue
        padded.append(item)
        existing_texts.add(text)

    extra = 1
    while len(padded) < target:
        text = f"How relevant is this topic to you right now ({extra})?"
        if text.lower() not in existing_texts and text.lower() not in blocked:
            padded.append(
                ClassificationQuestionBrief(
                    question_text=text,
                    is_required=True,
                    options=["Not relevant", "Somewhat relevant", "Very relevant"],
                )
            )
            existing_texts.add(text.lower())
        extra += 1

    data.classification_questions = padded
    return data
