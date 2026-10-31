"""Caption hashtags: sensitive/spam blocklist and the publish-time curated hashtag block.

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
from dataclasses import dataclass

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

# Curated niche/location pool (mid-size, real tags; editable in Schedule -> Hashtags).
NICHE_POOL: tuple[str, ...] = (
    "buenosairesphotographer",
    "fotografobuenosaires",
    "fotografiaargentina",
    "retratoartistico",
    "retratos",
    "portraitmood",
    "portraitpage",
    "portraitsociety",
    "stilllifephoto",
    "stilllifephotography",
    "naturallightportrait",
    "moodyportraits",
    "colorportrait",
    "fineartportrait",
    "analogvibes",
)
# Black-and-white photos get two of these instead of the giant generic mono tags.
MONO_POOL: tuple[str, ...] = ("bnwportrait", "bnw_portraits", "blackandwhiteportrait", "monochromeportrait")
# Back-compat name (old general pool); the niche pool replaces it.
GENERAL_POOL = NICHE_POOL
# Ultra-generic giants: posts drown in them. Removed from generated blocks and from caption text.
DROPPED_GENERIC: frozenset[str] = frozenset(
    {
        "monochrome",
        "blackandwhite",
        "blackandwhitephotography",
        "bnw",
        "photography",
        "art",
        "fineartphotography",
    }
)
# Real content tags Gemini may contribute (2-3 per post). Anything else Gemini invents
# (e.g. "formandlight", "moodyports") is dropped. Extendable in Settings ("allow").
CONTENT_ALLOWLIST: tuple[str, ...] = (
    "stilllife",
    "stilllifephotography",
    "stilllifephoto",
    "foodphotography",
    "foodstyling",
    "flatlay",
    "botanical",
    "flowers",
    "portrait",
    "portraitphotography",
    "portraiture",
    "studioportrait",
    "environmentalportrait",
    "naturallight",
    "windowlight",
    "softlight",
    "lightandshadow",
    "shadowplay",
    "chiaroscuro",
    "goldenhour",
    "colorphotography",
    "colorgrading",
    "analogphotography",
    "filmphotography",
    "35mm",
    "mediumformat",
    "editorialphotography",
    "conceptualphotography",
    "minimalism",
    "buenosaires",
    "argentina",
)
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


@dataclass(frozen=True)
class HashtagPools:
    niche: tuple[str, ...] = NICHE_POOL
    mono: tuple[str, ...] = MONO_POOL
    allow: tuple[str, ...] = ()  # extra Gemini/operator tags allowed on top of CONTENT_ALLOWLIST

    def allowed(self) -> set[str]:
        return {*self.niche, *self.mono, *CONTENT_ALLOWLIST, *self.allow} - DROPPED_GENERIC

    def as_dict(self) -> dict[str, list[str]]:
        return {"niche": list(self.niche), "mono": list(self.mono), "allow": list(self.allow)}


DEFAULT_POOLS = HashtagPools()
POOL_MAX = 40


def pools_from_json(raw: str | None) -> HashtagPools:
    """Operator pools from config JSON; missing/empty lists fall back to the defaults."""
    if not raw:
        return DEFAULT_POOLS
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return DEFAULT_POOLS
    if not isinstance(data, dict):
        return DEFAULT_POOLS
    return sanitize_pools(data)


def sanitize_pools(data: dict) -> HashtagPools:
    def _list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        values = data.get(key)
        if not isinstance(values, list):
            return default
        cleaned = [t for t in clean_tags(values) if t not in DROPPED_GENERIC][:POOL_MAX]
        return tuple(cleaned) if cleaned or key == "allow" else default

    return HashtagPools(niche=_list("niche", NICHE_POOL), mono=_list("mono", MONO_POOL), allow=_list("allow", ()))


def is_mono(stored: Iterable[object]) -> bool:
    raw = [normalize(t) for t in stored]
    return any(any(h == t or t.startswith(h) for h in _MONO_HINTS) for t in raw if t)


def allowed_content_tags(stored: Iterable[object], pools: HashtagPools = DEFAULT_POOLS) -> list[str]:
    """Gemini's tags that pass the blocklist AND the allowlist (made-up tags are dropped)."""
    allowed = pools.allowed()
    return [t for t in clean_tags(stored) if t in allowed and t not in DROPPED_GENERIC]


def pick_tags(
    stored: Iterable[object],
    *,
    seed: str,
    count: int,
    exclude: Iterable[str] = (),
    pools: HashtagPools = DEFAULT_POOLS,
    mono: bool | None = None,
) -> list[str]:
    """2-3 allowlisted Gemini content tags + 2 mono-pool tags (B&W only) + niche pool, shuffled."""
    stored = list(stored)
    excluded = set(clean_tags(exclude))
    rng = random.Random(seed)
    content = [t for t in allowed_content_tags(stored, pools) if t not in excluded]
    rng.shuffle(content)
    content = content[: min(len(content), rng.choice((2, 3)))]
    mono = is_mono(stored) if mono is None else mono
    chosen: list[str] = list(content)
    if mono:
        mono_pool = [t for t in clean_tags(pools.mono) if t not in excluded and t not in chosen]
        rng.shuffle(mono_pool)
        chosen += mono_pool[:2]
    niche = [t for t in clean_tags(pools.niche) if t not in excluded and t not in chosen and t not in DROPPED_GENERIC]
    if not mono:
        # Colour photos never get the mono-flavoured niche tags.
        niche = [t for t in niche if not is_mono([t])]
    rng.shuffle(niche)
    for tag in niche:
        if len(chosen) >= count:
            break
        chosen.append(tag)
    chosen = chosen[:count]
    rng.shuffle(chosen)
    return chosen


_TAG_TOKEN = re.compile(r"^#[A-Za-z0-9_]+$")


def _split_tag_block(text: str) -> tuple[str, list[str]]:
    """Split trailing hashtag-only lines (the generated block) from the prose."""
    lines = (text or "").rstrip().split("\n")
    block: list[str] = []
    while lines:
        tokens = lines[-1].split()
        if tokens and all(_TAG_TOKEN.match(tok) for tok in tokens):
            block = [normalize(tok) for tok in tokens] + block
            lines.pop()
            continue
        if not tokens and block:
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip(), block


def _strip_dropped_from_text(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        return "" if normalize(match.group(1)) in DROPPED_GENERIC else match.group(0)

    cleaned = _IN_TEXT.sub(_sub, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def compose_caption(
    text: str,
    stored: Iterable[object],
    *,
    seed: str,
    pools: HashtagPools | None = None,
    mono: bool | None = None,
) -> str:
    """Caption text + curated hashtag block (what is published).

    * blocklisted tags are removed everywhere (text and block), plus the ultra-generic giants;
    * the trailing hashtag-only block keeps only allowlisted tags (pools + content allowlist);
    * the block is topped up to 5-8 pool tags plus 2-3 allowlisted Gemini tags (max 10), shuffled
      with a per-photo seed so it varies per post. Idempotent for its own output.
    """
    pools = pools or DEFAULT_POOLS
    stored = list(stored)
    base = _strip_dropped_from_text(strip_blocked_from_text(text or ""))
    prose, block = _split_tag_block(base)
    allowed = pools.allowed()
    kept = [t for t in dict.fromkeys(block) if t in allowed and not is_blocked(t)]
    inline = tags_in_text(prose)
    rng = random.Random(f"{seed}:count")
    target = min(MAX_TAGS, rng.randint(MIN_TAGS, TARGET_TAGS) + 2)
    if len(kept) == len(block) and len(inline) + len(kept) >= MIN_TAGS:
        return base
    need = max(0, target - len(inline) - len(kept))
    extra = pick_tags(stored, seed=seed, count=need, exclude=[*inline, *kept], pools=pools, mono=mono)
    final = kept + extra
    if not final:
        return prose
    tag_line = " ".join(f"#{t}" for t in final)
    return f"{prose}\n\n{tag_line}" if prose else tag_line


__all__ = [
    "BLOCKED_EXACT",
    "CONTENT_ALLOWLIST",
    "DEFAULT_POOLS",
    "DROPPED_GENERIC",
    "MONO_POOL",
    "NICHE_POOL",
    "HashtagPools",
    "allowed_content_tags",
    "pools_from_json",
    "sanitize_pools",
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
