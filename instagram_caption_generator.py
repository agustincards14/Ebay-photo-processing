"""Instagram Carousel Caption Generator for Vintage Scanned Photos.

Formats a single cohesive caption for a 5-photo carousel post, detailing each
photograph sequentially ([1/5] through [5/5]) with aggregated hashtags while
strictly adhering to Instagram's 2,200 character limit.
"""

import re
import random
from typing import Any, Optional

MAX_INSTAGRAM_CAPTION_LENGTH = 2200
DEFAULT_BASE_HASHTAGS = [
    "#VintagePhotos",
    "#FoundPhotos",
    "#VernacularPhotography",
    "#VintageSnapshot",
    "#SnapshotPhotography",
    "#HistoricalPhotos",
    "#OldPhotographs",
]


def clean_hashtag(tag: str) -> str:
    """Formats text into a valid hashtag without spaces or punctuation."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", tag)
    return f"#{cleaned}" if cleaned else ""


def select_exact_hashtags(
    items_meta: list[dict[str, Any]],
    base_count: int = 2,
    themes_count: int = 2,
    subjects_count: int = 2,
    seed: Optional[int] = None,
) -> list[str]:
    """
    Selects exactly 6 hashtags per post:
      - 2 chosen randomly from DEFAULT_BASE_HASHTAGS.
      - 2 chosen randomly from the combined set of #themes across the photos.
      - 2 chosen randomly from the combined set of #subjects across the photos.
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    selected: list[str] = []
    seen_lower: set[str] = set()

    # 1. Base tags: 2 chosen randomly from community base tags
    base_pool = [t for t in DEFAULT_BASE_HASHTAGS]
    rng.shuffle(base_pool)
    for t in base_pool:
        if len(selected) < base_count:
            selected.append(t)
            seen_lower.add(t.lower())

    # 2. Themes: 2 chosen randomly from combined set of themes across all items
    themes_set: dict[str, str] = {}
    for item in items_meta:
        for theme in item.get("themes", []):
            ht = clean_hashtag(str(theme))
            if ht and ht.lower() not in seen_lower and ht.lower() not in themes_set:
                themes_set[ht.lower()] = ht

    themes_pool = sorted(list(themes_set.values()))
    rng.shuffle(themes_pool)
    for t in themes_pool:
        if len(selected) < base_count + themes_count:
            selected.append(t)
            seen_lower.add(t.lower())

    # 3. Subjects: 2 chosen randomly from combined set of subjects across all items
    subjects_set: dict[str, str] = {}
    for item in items_meta:
        for subj in item.get("subjects", []):
            ht = clean_hashtag(str(subj))
            if ht and ht.lower() not in seen_lower and ht.lower() not in subjects_set:
                subjects_set[ht.lower()] = ht

    subjects_pool = sorted(list(subjects_set.values()))
    rng.shuffle(subjects_pool)
    for t in subjects_pool:
        if len(selected) < base_count + themes_count + subjects_count:
            selected.append(t)
            seen_lower.add(t.lower())

    # 4. Fallback safeguard: if themes or subjects had fewer than 2, fill from remaining base tags or subjects
    target_count = base_count + themes_count + subjects_count
    if len(selected) < target_count:
        fallback_pool = [t for t in DEFAULT_BASE_HASHTAGS if t.lower() not in seen_lower] + \
                        [t for t in subjects_pool if t.lower() not in seen_lower] + \
                        [t for t in themes_pool if t.lower() not in seen_lower]
        rng.shuffle(fallback_pool)
        for t in fallback_pool:
            if t.lower() not in seen_lower:
                selected.append(t)
                seen_lower.add(t.lower())
            if len(selected) >= target_count:
                break

    return selected[:target_count]


def build_carousel_caption(
    items_meta: list[dict[str, Any]],
    seed: Optional[int] = None,
) -> str:
    """
    Builds a sequential caption starting directly with [1/5] and ending with exactly 6 hashtags.
    
    Args:
        items_meta: List of metadata dictionaries (typically 5 items).
        seed: Optional RNG seed for deterministic testing.
        
    Returns:
        Formatted caption string under 2,200 characters starting directly with [1/5].
    """
    total_slides = len(items_meta)

    # 1. Select Exactly 6 Hashtags (2 base, 2 themes, 2 subjects)
    hashtags_list = select_exact_hashtags(items_meta, seed=seed)
    hashtags_str = "\n\n🏷️ " + " ".join(hashtags_list)

    # 2. Build Slide Sections
    # Reserve room for hashtags + safety buffer
    reserved_chars = len(hashtags_str) + 30
    available_for_slides = MAX_INSTAGRAM_CAPTION_LENGTH - reserved_chars
    per_slide_budget = max(80, available_for_slides // max(1, total_slides))

    slide_blocks = []
    for idx, item in enumerate(items_meta, start=1):
        year = item.get("year", "Vintage").strip()
        title = item.get("title", f"Photo #{idx}").strip()
        desc = item.get("description", "").strip()
        location = item.get("location", "").strip()

        # Format header for this slide
        slide_head = f"[{idx}/{total_slides}] {year}: {title}"
        if location and location not in ("Unknown", "United States"):
            slide_head += f" (📍 {location})"

        # Calculate remaining budget for description
        desc_budget = max(0, per_slide_budget - len(slide_head) - 10)
        if desc and len(desc) > desc_budget:
            # Truncate description cleanly at word boundary
            truncated_desc = desc[:desc_budget].rsplit(" ", 1)[0].rstrip(",.;:") + "..."
        else:
            truncated_desc = desc

        if truncated_desc:
            slide_block = f"{slide_head}\n{truncated_desc}"
        else:
            slide_block = slide_head

        slide_blocks.append(slide_block)

    body = "\n\n".join(slide_blocks)
    full_caption = f"{body}{hashtags_str}"

    # Final emergency truncate if still exceeds limit
    if len(full_caption) > MAX_INSTAGRAM_CAPTION_LENGTH:
        overflow = len(full_caption) - MAX_INSTAGRAM_CAPTION_LENGTH
        # Trim from body
        body = body[: -overflow - 3].rsplit(" ", 1)[0] + "..."
        full_caption = f"{body}{hashtags_str}"

    return full_caption
