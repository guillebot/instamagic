"""Daily Engage list: posts the operator may open and engage with by hand.

Hard guarantee: nothing in this module likes, follows, comments, or sends anything to other
accounts. It only reads public Graph data (hashtag search, Business Discovery) and stores a list.
Gemini gets caption text only (never pixels, never library data).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from instamagic.errors import NotAllowed, NotConfigured
from instamagic.logging_config import get_logger
from instamagic.models import (
    EngageRun,
    EngageSettings,
    EngageTarget,
    HashtagResearchCache,
    HashtagResearchEvent,
    Image,
    InstagramMetrics,
    InstagramPost,
    PostingScheduleConfig,
    RelatedAccount,
)
from instamagic.services.instagram.client import InstagramClient
from instamagic.services.instagram.hashtag_research import (
    HASHTAG_BUDGET_DAYS,
    budget_status,
    normalize_hashtag,
    research_hashtag,
)
from instamagic.services.instagram.permissions import classify_graph_error_detail, parse_scopes_json
from instamagic.services.instagram.store import get_connection
from sqlalchemy import func, select
from sqlalchemy.orm import Session

log = get_logger("engage")

SETTINGS_ID = "default"
DEFAULT_TZ = "America/Buenos_Aires"
MAX_NICHE_TAGS = 8
BUDGET_RESERVE = 6  # leave room in the 30/7d hashtag budget for manual research
MAX_ACCOUNTS_PER_RUN = 12
ACCOUNT_POST_MAX_AGE_DAYS = 21
HASHTAG_POST_MAX_AGE_DAYS = 7
MAX_LIKES_FOR_TARGET = 3000
GRAPH_PAUSE_SECONDS = 0.4
RUN_STALE_MINUTES = 30
NEEDED_SCOPES = ("instagram_basic", "pages_read_engagement")

# Too broad to be a niche signal (and spam magnets).
GENERIC_TAGS = {
    "photography",
    "photo",
    "photooftheday",
    "picoftheday",
    "instagood",
    "instagram",
    "love",
    "art",
    "nature",
    "beautiful",
    "follow",
    "like",
    "likeforlike",
    "followforfollow",
    "f4f",
    "l4l",
    "insta",
    "photographer",
    "photos",
    "pic",
    "pics",
    "travel",
    "happy",
    "style",
    "fashion",
    "explore",
    "explorepage",
    "viral",
    "reels",
    "reel",
    "trending",
    "fyp",
}
FALLBACK_TAGS = [
    "fineartphotography",
    "availablelight",
    "moodyportraits",
    "quietphotography",
    "blackandwhitephotography",
]
SPAM_MARKERS = (
    "follow for follow",
    "follow4follow",
    "f4f",
    "l4l",
    "like for like",
    "giveaway",
    "dm for promo",
    "dm for collab",
    "promo",
    "onlyfans",
    "crypto",
    "forex",
    "check my bio",
    "link in bio",
)
_HASHTAG_IN_TEXT = re.compile(r"#([A-Za-z0-9_]{2,64})")
_MENTION_IN_TEXT = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{2,30})")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


@dataclass
class Candidate:
    source: str  # "account" | "hashtag"
    source_value: str
    permalink: str
    ig_media_id: str | None
    media_url: str | None
    media_type: str | None
    username: str | None
    caption: str
    like_count: int | None
    comments_count: int | None
    posted_at: datetime | None
    reason: str
    score: float = 0.0


@dataclass
class RunNotes:
    hashtags: list[dict[str, object]] = field(default_factory=list)
    accounts: list[dict[str, object]] = field(default_factory=list)
    suggested_accounts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    drafts: str = "none"


# ---------------------------------------------------------------- settings


def get_or_create_settings(session: Session) -> EngageSettings:
    row = session.get(EngageSettings, SETTINGS_ID)
    if row is None:
        row = EngageSettings(id=SETTINGS_ID)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def settings_out(row: EngageSettings) -> dict[str, object]:
    return {
        "enabled": bool(row.enabled),
        "daily_target": int(row.daily_target),
        "account_cooldown_days": int(row.account_cooldown_days),
        "run_hour_local": int(row.run_hour_local),
        "hashtags_per_run": int(row.hashtags_per_run),
        "seed_hashtags": _json_list(row.seed_hashtags_json),
    }


def update_settings(
    session: Session,
    *,
    enabled: bool | None = None,
    daily_target: int | None = None,
    account_cooldown_days: int | None = None,
    run_hour_local: int | None = None,
    hashtags_per_run: int | None = None,
    seed_hashtags: list[str] | None = None,
) -> EngageSettings:
    row = get_or_create_settings(session)
    if enabled is not None:
        row.enabled = enabled
    if daily_target is not None:
        row.daily_target = max(5, min(30, int(daily_target)))
    if account_cooldown_days is not None:
        row.account_cooldown_days = max(1, min(60, int(account_cooldown_days)))
    if run_hour_local is not None:
        row.run_hour_local = max(0, min(23, int(run_hour_local)))
    if hashtags_per_run is not None:
        row.hashtags_per_run = max(1, min(5, int(hashtags_per_run)))
    if seed_hashtags is not None:
        cleaned: list[str] = []
        for raw in seed_hashtags:
            if not str(raw).strip():
                continue
            tag = normalize_hashtag(str(raw))
            if tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) > MAX_NICHE_TAGS:
            raise NotAllowed(
                f"At most {MAX_NICHE_TAGS} seed hashtags (the Graph API allows 30 unique per 7 days)."
            )
        row.seed_hashtags_json = json.dumps(cleaned)
    session.commit()
    session.refresh(row)
    return row


def local_tz(session: Session) -> ZoneInfo:
    cfg = session.get(PostingScheduleConfig, "default")
    name = (cfg.timezone if cfg and cfg.timezone else "") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def local_today(session: Session, now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(local_tz(session)).date().isoformat()


# ---------------------------------------------------------------- niche hashtags


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _clean_tag(raw: str) -> str | None:
    try:
        tag = normalize_hashtag(raw)
    except NotAllowed:
        return None
    return None if tag in GENERIC_TAGS else tag


def niche_hashtags(session: Session, settings_row: EngageSettings) -> list[dict[str, str]]:
    """Ranked niche tags with a short why. Seeds first, then best-performing, then most-used."""
    ranked: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(tag: str | None, why: str) -> None:
        if tag and tag not in seen and len(ranked) < MAX_NICHE_TAGS:
            seen.add(tag)
            ranked.append({"tag": tag, "why": why})

    for raw in _json_list(settings_row.seed_hashtags_json):
        add(_clean_tag(raw), "your seed hashtag")

    # Hashtags on published posts, weighted by best reach/interactions.
    perf: Counter[str] = Counter()
    published_image_ids: set[object] = set()
    posts = session.scalars(select(InstagramPost).where(InstagramPost.status == "published")).all()
    for post in posts:
        if post.image_id is not None:
            published_image_ids.add(post.image_id)
        metric = session.scalars(
            select(InstagramMetrics)
            .where(InstagramMetrics.post_id == post.id)
            .order_by(InstagramMetrics.captured_at.desc())
            .limit(1)
        ).first()
        weight = 1.0
        if metric is not None:
            weight += float(metric.reach or 0) / 100.0 + float(metric.total_interactions or 0) / 5.0
        for raw in _HASHTAG_IN_TEXT.findall(post.caption or ""):
            tag = _clean_tag(raw)
            if tag:
                perf[tag] += weight
    for tag, _ in perf.most_common():
        add(tag, "on your best-performing posts")

    # Gemini enrichment hashtags of your (safe, enriched) frames — published ones count triple.
    used: Counter[str] = Counter()
    rows = session.execute(
        select(Image.id, Image.enrich_hashtags_json)
        .where(Image.enrich_hashtags_json.is_not(None))
        .limit(5000)
    ).all()
    for image_id, blob in rows:
        weight = 3 if image_id in published_image_ids else 1
        for raw in _json_list(blob):
            tag = _clean_tag(raw)
            if tag:
                used[tag] += weight
    for tag, _ in used.most_common():
        add(tag, "most used on your frames")

    for tag in FALLBACK_TAGS:
        add(tag, "photography niche default")
    return ranked


def _searched_recently(session: Session) -> set[str]:
    since = datetime.now(UTC) - timedelta(days=HASHTAG_BUDGET_DAYS)
    rows = session.scalars(
        select(HashtagResearchEvent.hashtag).where(
            HashtagResearchEvent.action == "search", HashtagResearchEvent.created_at >= since
        )
    ).all()
    return set(rows)


def pick_run_hashtags(
    session: Session, niche: list[dict[str, str]], per_run: int, day_index: int
) -> list[dict[str, str]]:
    """Rotate through the niche list; new (budget-counted) tags only while budget has a reserve."""
    if not niche:
        return []
    searched = _searched_recently(session)
    remaining = budget_status(session)["remaining_unique_hashtags_7d"]
    start = (day_index * per_run) % len(niche)
    rotated = niche[start:] + niche[:start]
    picked: list[dict[str, str]] = []
    for item in rotated:
        if len(picked) >= per_run:
            break
        tag = item["tag"]
        if tag in searched:
            picked.append(item)
        elif remaining > BUDGET_RESERVE:
            picked.append(item)
            remaining -= 1
    return picked


# ---------------------------------------------------------------- candidates


def _parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    text = str(raw)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_spammy(caption: str) -> bool:
    lowered = caption.lower()
    if any(marker in lowered for marker in SPAM_MARKERS):
        return True
    return len(_HASHTAG_IN_TEXT.findall(caption)) > 25


def _age_text(posted_at: datetime | None, now: datetime) -> str:
    if posted_at is None:
        return "recent"
    hours = max(0, int((now - posted_at).total_seconds() // 3600))
    if hours < 1:
        return "under 1h ago"
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _score(likes: int | None, posted_at: datetime | None, now: datetime) -> float:
    score = 0.0
    if posted_at is not None:
        age_h = max(0.0, (now - posted_at).total_seconds() / 3600)
        score += max(0.0, 48 - age_h) / 4  # fresher posts first (comments get seen)
    if likes is not None:
        # Small-to-mid creators notice and reciprocate; huge posts bury comments.
        if 5 <= likes <= 400:
            score += 6
        elif likes < 5:
            score += 3
        elif likes <= 1500:
            score += 2
    return score


def _media_ok(row: dict, *, max_age_days: int, now: datetime) -> bool:
    if not row.get("permalink"):
        return False
    if str(row.get("media_type") or "IMAGE").upper() not in {"IMAGE", "CAROUSEL_ALBUM"}:
        return False
    likes = row.get("like_count")
    if isinstance(likes, int) and likes > MAX_LIKES_FOR_TARGET:
        return False
    if _is_spammy(str(row.get("caption") or "")):
        return False
    posted = _parse_ts(row.get("timestamp"))
    return not (posted is not None and posted < now - timedelta(days=max_age_days))


def hashtag_candidates(
    session: Session,
    client: InstagramClient,
    tags: list[dict[str, str]],
    notes: RunNotes,
    now: datetime,
) -> tuple[list[Candidate], str | None]:
    """Returns candidates and an error code if the Graph denied hashtag search."""
    out: list[Candidate] = []
    blocked: str | None = None
    for item in tags:
        tag = item["tag"]
        cached = session.get(HashtagResearchCache, tag)
        needs_graph = cached is None or _aware(cached.fetched_at) < now - timedelta(hours=24)
        try:
            data = research_hashtag(session, client, tag)
        except NotAllowed as exc:
            notes.hashtags.append({"tag": tag, "why": item["why"], "error": str(exc.detail)})
            continue
        except NotConfigured as exc:
            detail = str(exc.detail)
            kind = classify_graph_error_detail(detail)
            notes.hashtags.append({"tag": tag, "why": item["why"], "error": _short(detail)})
            if kind in {"permission", "auth"}:
                blocked = "hashtag_permission" if kind == "permission" else "auth"
                break
            continue
        finally:
            if needs_graph:
                time.sleep(GRAPH_PAUSE_SECONDS)
        rows_seen = 0
        for edge, rows in (
            ("recent", data.get("recent_media") or []),
            ("top", data.get("top_media") or []),
        ):
            for row in rows:
                if not isinstance(row, dict) or not _media_ok(
                    row, max_age_days=HASHTAG_POST_MAX_AGE_DAYS, now=now
                ):
                    continue
                rows_seen += 1
                posted = _parse_ts(row.get("timestamp"))
                likes = row.get("like_count") if isinstance(row.get("like_count"), int) else None
                label = "Recent" if edge == "recent" else "Top"
                reason = f"{label} post in #{tag} ({item['why']}) · {_age_text(posted, now)}"
                if likes is not None:
                    reason += f" · {likes} likes"
                out.append(
                    Candidate(
                        source="hashtag",
                        source_value=tag,
                        permalink=str(row["permalink"]),
                        ig_media_id=str(row["id"]) if row.get("id") else None,
                        media_url=row.get("media_url"),
                        media_type=row.get("media_type"),
                        username=None,
                        caption=str(row.get("caption") or ""),
                        like_count=likes,
                        comments_count=row.get("comments_count")
                        if isinstance(row.get("comments_count"), int)
                        else None,
                        posted_at=posted,
                        reason=reason,
                        score=_score(likes, posted, now) + (2 if edge == "recent" else 0),
                    )
                )
                for mention in _MENTION_IN_TEXT.findall(str(row.get("caption") or "")):
                    handle = mention.lower().rstrip(".")
                    if handle and handle not in notes.suggested_accounts:
                        notes.suggested_accounts.append(handle)
        notes.hashtags.append({"tag": tag, "why": item["why"], "candidates": rows_seen})
    return out, blocked


def _recently_targeted_accounts(session: Session, cooldown_days: int, now: datetime) -> set[str]:
    since = now - timedelta(days=cooldown_days)
    rows = session.scalars(
        select(EngageTarget.username).where(
            EngageTarget.username.is_not(None), EngageTarget.created_at >= since
        )
    ).all()
    return {str(r).lower() for r in rows}


def account_candidates(
    session: Session,
    client: InstagramClient,
    *,
    cooldown_days: int,
    known_permalinks: set[str],
    notes: RunNotes,
    now: datetime,
) -> tuple[list[Candidate], str | None]:
    accounts = session.scalars(select(RelatedAccount).order_by(RelatedAccount.created_at)).all()
    if not accounts:
        return [], None
    cooling = _recently_targeted_accounts(session, cooldown_days, now)
    fetch = getattr(client, "fetch_business_discovery", None)
    if not callable(fetch):
        return [], None
    out: list[Candidate] = []
    blocked: str | None = None
    fetched = 0
    for account in accounts:
        handle = account.handle.lstrip("@").lower()
        if handle in cooling:
            notes.accounts.append(
                {"handle": handle, "skipped": f"engaged in the last {cooldown_days} days"}
            )
            continue
        if fetched >= MAX_ACCOUNTS_PER_RUN:
            break
        fetched += 1
        try:
            discovery = fetch(handle)
        except NotConfigured as exc:
            detail = str(exc.detail)
            kind = classify_graph_error_detail(detail)
            notes.accounts.append({"handle": handle, "error": _short(detail)})
            if kind == "auth":
                blocked = "auth"
                break
            continue
        finally:
            time.sleep(GRAPH_PAUSE_SECONDS)
        media = (discovery.get("media") or {}).get("data") or []
        followers = discovery.get("followers_count")
        best: Candidate | None = None
        for row in media:
            if not isinstance(row, dict) or not _media_ok(
                row, max_age_days=ACCOUNT_POST_MAX_AGE_DAYS, now=now
            ):
                continue
            if str(row["permalink"]) in known_permalinks:
                continue
            posted = _parse_ts(row.get("timestamp"))
            likes = row.get("like_count") if isinstance(row.get("like_count"), int) else None
            reason = "Similar account"
            if account.why_relevant:
                reason += f" ({_short(account.why_relevant, 60)})"
            if isinstance(followers, int):
                reason += f" · {followers:,} followers"
            reason += f" · newest post {_age_text(posted, now)}"
            best = Candidate(
                source="account",
                source_value=handle,
                permalink=str(row["permalink"]),
                ig_media_id=str(row["id"]) if row.get("id") else None,
                media_url=row.get("media_url"),
                media_type=row.get("media_type"),
                username=str(row.get("username") or discovery.get("username") or handle).lower(),
                caption=str(row.get("caption") or ""),
                like_count=likes,
                comments_count=row.get("comments_count")
                if isinstance(row.get("comments_count"), int)
                else None,
                posted_at=posted,
                reason=reason,
                score=_score(likes, posted, now) + 4,
            )
            break  # newest eligible post only: one per account
        notes.accounts.append({"handle": handle, "candidate": best is not None})
        if best is not None:
            out.append(best)
    return out, blocked


# ---------------------------------------------------------------- drafts


def _sanitize_caption(text: str) -> str:
    cleaned = _URL_RE.sub("", text)
    cleaned = re.sub(r"originals", "original", cleaned, flags=re.I)
    cleaned = (
        cleaned.replace("/photos/", " photos ")
        .replace("\\photos\\", " photos ")
        .replace("file:", "file ")
    )
    return re.sub(r"\s+", " ", cleaned).strip()[:400]


def draft_comments(
    growth: object, targets: list[EngageTarget], *, account_voice: str, notes: RunNotes
) -> None:
    drafter = getattr(growth, "draft_engage_comments", None)
    if not callable(drafter) or not targets:
        notes.drafts = "unavailable"
        return
    from instamagic.services.gemini.growth import EngageTargetText

    payload = [
        EngageTargetText(
            id=str(index),
            account=target.username or "",
            hashtag=target.source_value if target.source == "hashtag" else "",
            caption=_sanitize_caption(target.caption or ""),
        )
        for index, target in enumerate(targets)
    ]
    try:
        batch = drafter(payload, account_voice=account_voice)
    except Exception as exc:  # Gemini down/blocked must never break the list
        notes.drafts = f"failed: {type(exc).__name__}"
        log.info("engage_drafts_failed", error=type(exc).__name__)
        return
    by_id = {c.id: c.text for c in batch.comments}
    filled = 0
    for index, target in enumerate(targets):
        text = by_id.get(str(index))
        if text:
            target.suggestion = text
            filled += 1
    notes.drafts = f"{filled}/{len(targets)}"


# ---------------------------------------------------------------- run


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _own_username(session: Session) -> str | None:
    row = get_connection(session)
    if row is None or not row.account_json:
        return None
    try:
        data = json.loads(row.account_json)
    except json.JSONDecodeError:
        return None
    name = data.get("username") if isinstance(data, dict) else None
    return str(name).lower() if name else None


def running_run(session: Session, now: datetime | None = None) -> EngageRun | None:
    now = now or datetime.now(UTC)
    row = session.scalars(
        select(EngageRun)
        .where(EngageRun.status == "running")
        .order_by(EngageRun.started_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    started = _aware(row.started_at)
    if started < now - timedelta(minutes=RUN_STALE_MINUTES):
        row.status = "failed"
        row.error_code = "stale"
        row.error = "Run did not finish (worker restarted)."
        row.finished_at = now
        session.commit()
        return None
    return row


def run_engage(
    session: Session,
    client: InstagramClient,
    growth: object,
    *,
    account_voice: str,
    trigger: str = "schedule",
    now: datetime | None = None,
) -> EngageRun:
    now = now or datetime.now(UTC)
    settings_row = get_or_create_settings(session)
    day = local_today(session, now)
    run = EngageRun(run_date=day, trigger=trigger, status="running", started_at=now)
    session.add(run)
    session.commit()
    notes = RunNotes()
    try:
        _run_into(
            session, client, growth, run, settings_row, notes, account_voice=account_voice, now=now
        )
    except Exception as exc:
        session.rollback()
        run = session.get(EngageRun, run.id) or run
        run.status = "failed"
        run.error_code = "error"
        run.error = _short(getattr(exc, "detail", None) or str(exc) or type(exc).__name__)
        log.info("engage_run_failed", error=type(exc).__name__)
    run.detail_json = json.dumps(notes.__dict__, ensure_ascii=False, default=str)
    run.finished_at = datetime.now(UTC)
    session.commit()
    session.refresh(run)
    log.info(
        "engage_run_finished", status=run.status, created=run.targets_created, code=run.error_code
    )
    return run


def _run_into(
    session: Session,
    client: InstagramClient,
    growth: object,
    run: EngageRun,
    settings_row: EngageSettings,
    notes: RunNotes,
    *,
    account_voice: str,
    now: datetime,
) -> None:
    if not client.configured():
        run.status = "blocked"
        run.error_code = "not_connected"
        run.error = "Instagram is not connected."
        return
    target_total = int(settings_row.daily_target)
    already_today = (
        session.scalar(
            select(func.count(EngageTarget.id)).where(EngageTarget.run_date == run.run_date)
        )
        or 0
    )
    wanted = max(0, target_total - int(already_today))
    if wanted == 0:
        run.status = "ok"
        notes.warnings.append("Today's list is already full.")
        return

    known_permalinks = set(session.scalars(select(EngageTarget.permalink)).all())
    own = _own_username(session)
    own_media = {m for m in session.scalars(select(InstagramPost.ig_media_id)).all() if m}

    acct, acct_blocked = account_candidates(
        session,
        client,
        cooldown_days=int(settings_row.account_cooldown_days),
        known_permalinks=known_permalinks,
        notes=notes,
        now=now,
    )
    if acct_blocked == "auth":
        run.status = "blocked"
        run.error_code = "auth"
        run.error = "Instagram token expired or was revoked. Reconnect Instagram."
        return

    niche = niche_hashtags(session, settings_row)
    day_index = datetime.fromisoformat(run.run_date).toordinal()
    tags = pick_run_hashtags(session, niche, int(settings_row.hashtags_per_run), day_index)
    tag_cands, tag_blocked = hashtag_candidates(session, client, tags, notes, now)

    def usable(c: Candidate) -> bool:
        if c.permalink in known_permalinks:
            return False
        if own and c.username == own:
            return False
        return not (c.ig_media_id and c.ig_media_id in own_media)

    acct = sorted((c for c in acct if usable(c)), key=lambda c: c.score, reverse=True)
    tag_sorted = sorted((c for c in tag_cands if usable(c)), key=lambda c: c.score, reverse=True)

    chosen: list[Candidate] = []
    seen: set[str] = set()
    account_share = max(0, wanted // 2)
    for c in acct[:account_share]:
        chosen.append(c)
        seen.add(c.permalink)
    # Spread hashtag picks across tags (round-robin) so one tag does not dominate.
    buckets: dict[str, list[Candidate]] = {}
    for c in tag_sorted:
        buckets.setdefault(c.source_value, []).append(c)
    while len(chosen) < wanted and any(buckets.values()):
        for tag in list(buckets):
            if len(chosen) >= wanted:
                break
            if not buckets[tag]:
                continue
            c = buckets[tag].pop(0)
            if c.permalink not in seen:
                chosen.append(c)
                seen.add(c.permalink)
    for c in acct[account_share:]:
        if len(chosen) >= wanted:
            break
        if c.permalink not in seen:
            chosen.append(c)
            seen.add(c.permalink)

    rows: list[EngageTarget] = []
    for c in chosen:
        row = EngageTarget(
            run_id=run.id,
            run_date=run.run_date,
            source=c.source,
            source_value=c.source_value[:128],
            ig_media_id=c.ig_media_id,
            permalink=c.permalink[:512],
            media_url=c.media_url,
            media_type=c.media_type,
            username=c.username,
            caption=c.caption[:2200],
            like_count=c.like_count,
            comments_count=c.comments_count,
            posted_at=c.posted_at,
            reason=c.reason,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    draft_comments(growth, rows, account_voice=account_voice, notes=notes)

    existing_handles = {
        a.handle.lstrip("@").lower() for a in session.scalars(select(RelatedAccount)).all()
    }
    notes.suggested_accounts = [
        h for h in notes.suggested_accounts if h not in existing_handles and h != own
    ][:12]

    run.targets_created = len(rows)
    if tag_blocked and not rows:
        run.status = "blocked"
        run.error_code = tag_blocked
        run.error = (
            "Instagram denied hashtag search. Reconnect Instagram with all permissions "
            "(the Meta app also needs Instagram Public Content Access for hashtags)."
            if tag_blocked == "hashtag_permission"
            else "Instagram token expired or was revoked. Reconnect Instagram."
        )
        return
    if tag_blocked:
        run.error_code = tag_blocked
        notes.warnings.append("Hashtag search was denied; list built from similar accounts only.")
    if not rows and not acct and not tags:
        notes.warnings.append("No hashtags or similar accounts to search yet.")
    failed = [h for h in notes.hashtags if h.get("error")]
    if not rows and failed and len(failed) == len(notes.hashtags) and not notes.accounts:
        run.status = "blocked"
        run.error_code = "hashtag_failed"
        run.error = "Hashtag search failed: " + str(failed[0]["error"])[:200]
        return
    run.status = "ok"


# ---------------------------------------------------------------- views


def target_out(row: EngageTarget) -> dict[str, object]:
    caption = " ".join((row.caption or "").split())
    return {
        "id": str(row.id),
        "run_date": row.run_date,
        "source": row.source,
        "source_value": row.source_value,
        "username": row.username,
        "permalink": row.permalink,
        "media_url": row.media_url,
        "media_type": row.media_type,
        "caption_snippet": caption[:220] + ("…" if len(caption) > 220 else ""),
        "like_count": row.like_count,
        "comments_count": row.comments_count,
        "posted_at": row.posted_at.isoformat() if row.posted_at else None,
        "reason": row.reason,
        "suggestion": row.suggestion,
        "status": row.status,
        "acted_at": row.acted_at.isoformat() if row.acted_at else None,
    }


def run_out(row: EngageRun | None) -> dict[str, object] | None:
    if row is None:
        return None
    try:
        detail = json.loads(row.detail_json or "{}")
    except json.JSONDecodeError:
        detail = {}
    return {
        "id": str(row.id),
        "run_date": row.run_date,
        "trigger": row.trigger,
        "status": row.status,
        "error_code": row.error_code,
        "error": row.error,
        "targets_created": row.targets_created,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "detail": detail,
    }


def permission_status(session: Session, client: InstagramClient) -> dict[str, object]:
    if not client.configured():
        return {
            "ok": False,
            "code": "not_connected",
            "missing": [],
            "message": "Instagram is not connected.",
        }
    row = get_connection(session)
    scopes = parse_scopes_json(row.token_scopes_json if row else None)
    missing = [s for s in NEEDED_SCOPES if scopes and s not in scopes]
    if missing:
        return {
            "ok": False,
            "code": "permission_missing",
            "missing": missing,
            "message": "Reconnect Instagram and grant: " + ", ".join(missing) + ".",
        }
    return {"ok": True, "code": None, "missing": [], "message": None}


def set_target_status(session: Session, target: EngageTarget, status: str) -> EngageTarget:
    if status not in {"open", "done", "skipped"}:
        raise NotAllowed("Status must be open, done, or skipped.")
    target.status = status
    target.acted_at = None if status == "open" else datetime.now(UTC)
    session.commit()
    session.refresh(target)
    return target


def overview(session: Session, client: InstagramClient) -> dict[str, object]:
    settings_row = get_or_create_settings(session)
    today = local_today(session)
    running = running_run(session)
    last = session.scalars(
        select(EngageRun)
        .where(EngageRun.status != "running")
        .order_by(EngageRun.started_at.desc())
        .limit(1)
    ).first()
    list_date = today
    targets = session.scalars(
        select(EngageTarget).where(EngageTarget.run_date == today).order_by(EngageTarget.created_at)
    ).all()
    if not targets:
        latest_date = session.scalar(select(func.max(EngageTarget.run_date)))
        if latest_date:
            list_date = str(latest_date)
            targets = session.scalars(
                select(EngageTarget)
                .where(EngageTarget.run_date == list_date)
                .order_by(EngageTarget.created_at)
            ).all()
    days: list[dict[str, object]] = []
    rows = session.execute(
        select(EngageTarget.run_date, EngageTarget.status, func.count(EngageTarget.id))
        .group_by(EngageTarget.run_date, EngageTarget.status)
        .order_by(EngageTarget.run_date.desc())
    ).all()
    by_day: dict[str, dict[str, int]] = {}
    for day, status, count in rows:
        by_day.setdefault(str(day), {"open": 0, "done": 0, "skipped": 0})[str(status)] = int(count)
    for day in sorted(by_day, reverse=True)[:14]:
        counts = by_day[day]
        days.append(
            {
                "date": day,
                "done": counts.get("done", 0),
                "skipped": counts.get("skipped", 0),
                "total": sum(counts.values()),
            }
        )
    order = {"open": 0, "done": 1, "skipped": 2}
    targets_sorted = sorted(targets, key=lambda t: order.get(t.status, 3))
    return {
        "today": today,
        "list_date": list_date,
        "settings": settings_out(settings_row),
        "permission": permission_status(session, client),
        "running": running is not None,
        "last_run": run_out(last),
        "targets": [target_out(t) for t in targets_sorted],
        "days": days,
        "niche_hashtags": niche_hashtags(session, settings_row),
        "hashtag_budget": budget_status(session),
        "similar_accounts": [
            a.handle.lstrip("@")
            for a in session.scalars(select(RelatedAccount).order_by(RelatedAccount.created_at))
        ],
    }
