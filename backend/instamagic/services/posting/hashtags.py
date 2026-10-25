"""Caption hashtags: sensitive/spam blocklist and the publish-time hashtag block.

Background enrichment stores per-photo hashtags on ``Image.enrich_hashtags_json`` but captions are
stored separately; publishing used only ``Caption.text``, so hashtags never reached Instagram.
``compose_caption`` appends a varied 5-10 tag block (photo tags first, a few general photography tags
mixed in, seeded per image) and strips tags Instagram restricts or that signal adult/spam content.
It is idempotent: a caption that already carries enough hashtags (e.g. operator-edited) is kept.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Iterable

MIN_TAGS = 5
TARGET_TAGS = 8
MAX_TAGS = 10
SPECIFIC_FIRST = 4

_VALID = re.compile(r"^[a-z0-9_]{2,48}$")
_IN_TEXT = re.compile(r"(?<![\w&])#([A-Za-z0-9_]+)")

# Exact-match tags (too short/ambiguous for substring matching).
BLOCKED_EXACT: frozenset[str] = frozenset(
    {
        "hot", "hottie", "girl", "girls", "babe", "babes", "model", "models", "modeling", "adult", "body",
        "bodies", "curves", "curvy", "skin", "legs", "booty", "ass", "butt", "thick", "bra", "panties",
        "underwear", "bed", "bedroom", "shower", "bath", "wet", "tease", "kink", "kinky", "bdsm", "18",
        "18plus", "instagood", "instadaily", "instalike", "instamood", "picoftheday", "photooftheday",
        "followme", "follow", "like", "likes", "tagsforlikes", "tflers", "l4l", "f4f", "s4s", "fff", "lfl",
        "likeforlike", "likeforlikes", "followforfollow", "followforfollowback", "followback", "like4like",
        "follow4follow", "liker", "explore", "explorepage", "viral", "trending", "fyp", "foryou",
        "girlsofinstagram", "beautifulgirls", "prettygirl", "cutegirl", "bodyform", "bodyshape",
    }
)
# Substrings: any tag containing one of these is dropped.
BLOCKED_SUBSTRINGS: tuple[str, ...] = (
    "sexy", "sexi", "sex", "nude", "nudity", "naked", "boudoir", "erotic", "erotica", "nsfw", "lingerie",
    "bikini", "sensual", "seduct", "seduc", "porn", "xxx", "topless", "bottomless", "onlyfans", "fansly",
    "fetish", "nipple", "boob", "busty", "lewd", "undress", "intimate", "hotgirl", "adultcontent",
    "playboy", "pinup", "glamourmodel", "bodyscape", "implied", "barely", "follow4", "like4", "4like",
    "4follow", "forfollow", "forlike", "followers", "likesfor", "spam", "gainpost", "shoutout",
)

# General, safe photography/art tags mixed in for discovery (never body/adult themed).
GENERAL_POOL: tuple[str, ...] = (
    "photography",
    "fineartphotography",
    "portraitphotography",
    "lightandshadow",
    "artphotography",
    "visualart",
    "photoart",
    "naturallight",
    "moodyphotography",
    "minimalphotography",
    "artisticphotography",
    "contemporaryphotography",
)
MONO_POOL: tuple[str, ...] = ("blackandwhite", "blackandwhitephotography", "monochrome", "bnw")
_MONO_HINTS = ("monochrome", "blackandwhite", "bnw", "bw")


def normalize(raw: object) -> str:
    return str(raw or "").strip().lstrip("#").lower().replace(" ", "")


def is_blocked(tag: str) -> bool:
    tag = normalize(tag)
    if not tag:
        return True
    if tag in BLOCKED_EXACT:
        return True
    return any(part in tag for part in BLOCKED_SUBSTRINGS)


def clean_tags(tags: Iterable[object]) -> list[str]:
    """Normalize, dedupe and drop invalid or blocklisted tags (order preserved)."""
    out: list[str] = []
    for raw in tags:
        tag = normalize(raw)
        if not _VALID.match(tag) or tag.isdigit() or is_blocked(tag) or tag in out:
            continue
        out.append(tag)
    return out


def tags_in_text(text: str) -> list[str]:
    return [normalize(m) for m in _IN_TEXT.findall(text or "")]


def strip_blocked_from_text(text: str) -> str:
    """Remove blocklisted #tags from caption text; other text is left alone."""

    def _sub(match: re.Match[str]) -> str:
        return "" if is_blocked(match.group(1)) else match.group(0)

    cleaned = _IN_TEXT.sub(_sub, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def parse_stored(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(v) for v in values] if isinstance(values, list) else []


def pick_tags(stored: Iterable[object], *, seed: str, count: int, exclude: Iterable[str] = ()) -> list[str]:
    """Photo-specific tags first, then a seeded mix of remaining photo tags and general tags."""
    excluded = set(clean_tags(exclude))
    specific = [t for t in clean_tags(stored) if t not in excluded]
    rng = random.Random(seed)
    chosen = specific[:SPECIFIC_FIRST]
    rest = specific[SPECIFIC_FIRST:]
    rng.shuffle(rest)
    mono = any(any(h == t or t.startswith(h) for h in _MONO_HINTS) for t in specific)
    pool = [t for t in GENERAL_POOL if t not in excluded and t not in specific]
    rng.shuffle(pool)
    if mono:
        mono_pool = [t for t in MONO_POOL if t not in excluded and t not in specific]
        rng.shuffle(mono_pool)
        pool = mono_pool[:1] + pool
    # Two general tags for discovery when the photo has its own; more when it has few.
    general_quota = 2 if len(specific) >= count - 2 else count - len(specific)
    general = pool[:general_quota]
    merged = chosen + rest[: max(0, count - len(chosen) - len(general))] + general
    for extra in rest + pool:
        if len(merged) >= count:
            break
        if extra not in merged:
            merged.append(extra)
    return merged[:count]


def compose_caption(text: str, stored: Iterable[object], *, seed: str) -> str:
    """Caption text with blocklisted tags removed and a 5-10 tag block appended when it has fewer than 5."""
    base = strip_blocked_from_text(text or "")
    existing = tags_in_text(base)
    if len(existing) >= MIN_TAGS:
        return base
    need = min(MAX_TAGS, TARGET_TAGS) - len(existing)
    extra = pick_tags(stored, seed=seed, count=need, exclude=existing)
    if not extra:
        return base
    block = " ".join(f"#{t}" for t in extra)
    return f"{base}\n\n{block}" if base else block


__all__ = [
    "BLOCKED_EXACT",
    "BLOCKED_SUBSTRINGS",
    "GENERAL_POOL",
    "MAX_TAGS",
    "MIN_TAGS",
    "TARGET_TAGS",
    "clean_tags",
    "compose_caption",
    "is_blocked",
    "parse_stored",
    "pick_tags",
    "strip_blocked_from_text",
    "tags_in_text",
]
