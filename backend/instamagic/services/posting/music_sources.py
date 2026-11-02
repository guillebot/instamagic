"""Royalty-free music import from official APIs with explicit licences (Openverse audio).

Policy (enforced here, at search *and* again at import from the API's own detail response):

* Only CC0 and CC BY (any version) — both allow commercial use, including social media Reels,
  without payment. Anything NonCommercial (NC), NoDerivatives (ND), ShareAlike (SA), the Public
  Domain Mark or an unknown licence is rejected. The licence is never guessed: it must come from
  the API response and its URL must be a creativecommons.org deed.
* CC BY Reels get a short credit line in the caption (``append_credit``) — no hashtags/mentions,
  within Instagram's 2,200 character caption limit.
* No scraping: only ``api.openverse.org`` is queried; the audio file is downloaded from the URL the
  API returns for that record (the provider's official file URL, e.g. Jamendo storage).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import MusicTrack
from sqlalchemy import select
from sqlalchemy.orm import Session

log = get_logger(__name__)

OPENVERSE_API = "https://api.openverse.org/v1"
USER_AGENT = "instamagic/1.0 (self-hosted photo publishing tool; Reel music import)"
SOURCE_OPENVERSE = "openverse"
ALLOWED_LICENSES = ("cc0", "by")
SEARCH_MIN_S = 20
SEARCH_MAX_S = 300
IMPORT_MIN_S = 30.0
MIN_BIT_RATE = 128_000
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
CAPTION_LIMIT = 2200
CREDIT_MAX = 150
DEFAULT_QUERIES = (
    "ambient instrumental",
    "cinematic piano",
    "lofi chill",
    "downtempo",
    "chill electronic",
)
AUTOREFILL_EVERY = timedelta(days=7)
AUTOREFILL_MAX_PER_RUN = 5
_VOCAL_TAGS = {"vocal", "vocals", "voice", "singer", "singing", "song", "female vocals", "male vocals", "rap"}
_EXT_BY_TYPE = {
    "mp3": ".mp3",
    "mp31": ".mp3",
    "mp32": ".mp3",
    "mpeg": ".mp3",
    "ogg": ".ogg",
    "oga": ".ogg",
    "flac": ".flac",
    "wav": ".wav",
    "m4a": ".m4a",
    "aac": ".aac",
}
_EXT_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
}


# --- licence policy -------------------------------------------------------------------------


def normalize_license(code: object) -> str:
    return re.sub(r"[^a-z0-9-]", "", str(code or "").strip().lower())


def license_allowed(code: object, license_url: object = None) -> bool:
    """CC0 or CC BY only (commercial social use, no payment). Rejects NC, ND, SA, PDM, unknown."""
    norm = normalize_license(code)
    if norm not in ALLOWED_LICENSES:
        return False
    parts = set(norm.split("-"))
    if parts & {"nc", "nd", "sa"}:
        return False
    if license_url is not None:
        url = str(license_url or "").lower()
        host = urlparse(url).hostname or ""
        if host not in ("creativecommons.org", "www.creativecommons.org"):
            return False
        path = urlparse(url).path
        if any(f"/{bad}" in path or f"-{bad}" in path for bad in ("nc", "nd", "sa")):
            return False
        expected = "/publicdomain/zero/" if norm == "cc0" else "/licenses/by/"
        if expected not in path:
            return False
    return True


def license_label(code: str | None, version: str | None) -> str | None:
    norm = normalize_license(code)
    if not norm:
        return None
    if norm == "cc0":
        return f"CC0 {version or '1.0'}".strip()
    return f"CC {norm.upper().replace('-', '-')} {version or ''}".strip()


def _clean_credit_text(text: object, limit: int = 60) -> str:
    cleaned = re.sub(r"[#@\n\r\t]+", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit].rstrip()


def credit_line(title: str | None, artist: str | None, code: str | None, version: str | None) -> str | None:
    """'🎵 Title — Artist (CC BY 4.0)' for CC BY tracks; None for CC0 / uploads (no credit required)."""
    norm = normalize_license(code)
    if norm != "by":
        return None
    title_txt = _clean_credit_text(title) or "Untitled"
    artist_txt = _clean_credit_text(artist) or "Unknown artist"
    line = f"🎵 {title_txt} — {artist_txt} ({license_label(code, version)})"
    return line[:CREDIT_MAX]


def _split_tag_block(text: str) -> tuple[str, str]:
    parts = text.rstrip().rsplit("\n\n", 1)
    if len(parts) == 2:
        tokens = parts[1].split()
        if tokens and all(tok.startswith("#") for tok in tokens):
            return parts[0].rstrip(), parts[1]
    return text.rstrip(), ""


def append_credit(caption: str, credit: str | None, *, limit: int = CAPTION_LIMIT) -> str:
    """Insert the music credit after the prose, before the trailing hashtag block. Idempotent.

    If the result would exceed Instagram's caption limit the prose is shortened (never the credit
    or the hashtag block)."""
    caption = caption or ""
    if not credit or credit in caption:
        return caption
    prose, tags = _split_tag_block(caption)
    tail = f"\n\n{tags}" if tags else ""

    def build(body: str) -> str:
        return f"{body}\n\n{credit}{tail}" if body else f"{credit}{tail}"

    out = build(prose)
    if len(out) > limit:
        room = limit - len(build(""))
        prose = prose[: max(0, room - 3)].rstrip() + "…" if room > 3 else ""
        out = build(prose)
    return out[:limit]


# --- Openverse --------------------------------------------------------------------------------


@dataclass
class Candidate:
    source: str
    source_id: str
    title: str
    artist: str | None
    provider: str | None
    landing_url: str | None
    audio_url: str
    license: str
    license_version: str | None
    license_url: str
    attribution: str | None
    duration_s: float | None
    genres: list[str]
    tags: list[str]
    filetype: str | None
    bit_rate: int | None

    @property
    def instrumental(self) -> bool:
        return "instrumental" in self.tags

    @property
    def vocals(self) -> bool:
        return bool(set(self.tags) & _VOCAL_TAGS)

    def out(self, imported: bool = False) -> dict[str, object]:
        return {
            "source": self.source,
            "id": self.source_id,
            "title": self.title,
            "artist": self.artist,
            "provider": self.provider,
            "source_url": self.landing_url,
            "license": self.license,
            "license_version": self.license_version,
            "license_label": license_label(self.license, self.license_version),
            "license_url": self.license_url,
            "attribution": self.attribution,
            "duration_s": self.duration_s,
            "genres": self.genres,
            "tags": self.tags[:12],
            "instrumental": self.instrumental,
            "vocals": self.vocals,
            "bit_rate": self.bit_rate,
            "preview_url": f"/api/music/sources/{self.source}/{self.source_id}/preview",
            "imported": imported,
        }


def _parse(row: dict[str, Any]) -> Candidate | None:
    """Openverse audio record -> Candidate, or None when it fails the licence / content policy."""
    if not isinstance(row, dict) or row.get("mature"):
        return None
    code = normalize_license(row.get("license"))
    license_url = str(row.get("license_url") or "")
    if not license_allowed(code, license_url):
        return None
    audio_url = str(row.get("url") or "")
    if urlparse(audio_url).scheme != "https":
        return None
    try:
        duration = float(row.get("duration")) / 1000.0 if row.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    tags = [str((t or {}).get("name") or "").strip().lower() for t in row.get("tags") or [] if isinstance(t, dict)]
    try:
        bit_rate = int(row["bit_rate"]) if row.get("bit_rate") else None
    except (TypeError, ValueError):
        bit_rate = None
    return Candidate(
        source=SOURCE_OPENVERSE,
        source_id=str(row.get("id") or ""),
        title=str(row.get("title") or "Untitled").strip()[:200],
        artist=(str(row.get("creator")).strip()[:200] if row.get("creator") else None),
        provider=row.get("provider") or row.get("source"),
        landing_url=row.get("foreign_landing_url"),
        audio_url=audio_url,
        license=code,
        license_version=(str(row.get("license_version"))[:8] if row.get("license_version") else None),
        license_url=license_url,
        attribution=row.get("attribution"),
        duration_s=duration,
        genres=[str(g).lower() for g in row.get("genres") or []],
        tags=[t for t in tags if t],
        filetype=(str(row.get("filetype")).lower() if row.get("filetype") else None),
        bit_rate=bit_rate,
    )


# Records seen in recent Openverse search responses (already licence-checked by _parse). Openverse's
# detail endpoint is slow (~30 s uncached) and sometimes 5xx; import falls back to the search record.
_RECENT_TTL_S = 3600.0
_RECENT: dict[str, tuple[float, Candidate]] = {}


def _remember(cands: list[Candidate]) -> None:
    import time

    now = time.monotonic()
    for key in [k for k, (at, _) in _RECENT.items() if now - at > _RECENT_TTL_S]:
        _RECENT.pop(key, None)
    for cand in cands:
        _RECENT[cand.source_id] = (now, cand)


def _recent(source_id: str) -> Candidate | None:
    import time

    hit = _RECENT.get(source_id)
    if hit and time.monotonic() - hit[0] <= _RECENT_TTL_S:
        return hit[1]
    return None


class OpenverseClient:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(75.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def search(self, query: str, *, page: int = 1, page_size: int = 20) -> list[Candidate]:
        params = {
            "q": query,
            "license": ",".join(ALLOWED_LICENSES),
            "license_type": "commercial",
            "category": "music",
            "mature": "false",
            "page": max(1, page),
            "page_size": max(1, min(page_size, 20)),
        }
        resp = self._http.get(f"{OPENVERSE_API}/audio/", params=params)
        if resp.status_code >= 500:  # Openverse cold cache: 502/504 on the first try is common
            resp = self._http.get(f"{OPENVERSE_API}/audio/", params=params)
        if resp.status_code == 429:
            raise NotAllowed("Openverse rate limit reached; try again in a few minutes.")
        resp.raise_for_status()
        rows = (resp.json() or {}).get("results") or []
        cands = [c for c in (_parse(r) for r in rows) if c is not None and c.source_id]
        _remember(cands)
        return cands

    def detail(self, source_id: str) -> Candidate:
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", source_id or ""):
            raise NotFound("Unknown Openverse track id.")
        resp = None
        for _attempt in range(2):
            try:
                resp = self._http.get(f"{OPENVERSE_API}/audio/{source_id}/")
            except httpx.TimeoutException:
                resp = None
                continue
            if resp.status_code < 500:
                break
        if resp is None or resp.status_code >= 500:
            cached = _recent(source_id)
            if cached is not None:
                return cached
            if resp is None:
                raise httpx.ReadTimeout("Openverse detail timed out")
        if resp.status_code == 404:
            raise NotFound("Track not found on Openverse.")
        resp.raise_for_status()
        cand = _parse(resp.json() or {})
        if cand is None:
            raise NotAllowed("That track's licence is not CC0 or CC BY (or it is marked mature); not imported.")
        return cand

    def stream(self, url: str):
        return self._http.stream("GET", url)


def _dupe_key(title: str | None, artist: str | None) -> str:
    def norm(text: str | None) -> str:
        text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
        text = re.sub(r"[\(\[].*?[\)\]]", " ", text)
        text = re.sub(r"\b(original|radio|edit|mix|remix|version|instrumental|extended|remastered)\b", " ", text)
        return re.sub(r"[^a-z0-9]+", "", text)

    return f"{norm(title)}|{norm(artist)}"


def imported_source_ids(session: Session) -> set[str]:
    return {sid for sid in session.scalars(select(MusicTrack.source_id).where(MusicTrack.source_id.is_not(None)))}


def _duplicate(session: Session, cand: Candidate) -> MusicTrack | None:
    hit = session.scalars(
        select(MusicTrack).where(MusicTrack.source == cand.source, MusicTrack.source_id == cand.source_id)
    ).first()
    if hit is not None:
        return hit
    key = _dupe_key(cand.title, cand.artist)
    for track in session.scalars(select(MusicTrack)).all():
        if _dupe_key(track.title, track.artist) == key:
            return track
    return None


def search(
    session: Session,
    query: str,
    *,
    min_s: float = SEARCH_MIN_S,
    max_s: float = SEARCH_MAX_S,
    page: int = 1,
    client: OpenverseClient | None = None,
) -> list[dict[str, object]]:
    query = re.sub(r"\s+", " ", (query or "").strip())[:120]
    if not query:
        raise NotAllowed("Type a mood or genre to search for.")
    min_s = max(SEARCH_MIN_S, float(min_s))
    max_s = min(SEARCH_MAX_S, float(max_s))
    client = client or OpenverseClient()
    have = imported_source_ids(session)
    out = []
    for cand in client.search(query, page=page):
        if cand.duration_s is None or not (min_s <= cand.duration_s <= max_s):
            continue
        out.append(cand.out(imported=cand.source_id in have))
    return out


def _extension(cand: Candidate, content_type: str | None) -> str:
    if cand.filetype and cand.filetype in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[cand.filetype]
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in _EXT_BY_MIME:
        return _EXT_BY_MIME[mime]
    suffix = Path(urlparse(cand.audio_url).path).suffix.lower()
    if suffix in {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac"}:
        return suffix
    raise NotAllowed("Unsupported audio format from the source.")


def import_track(
    session: Session,
    settings: Settings,
    source: str,
    source_id: str,
    *,
    enabled: bool = True,
    client: OpenverseClient | None = None,
) -> MusicTrack:
    """Re-read the record from the API (licence verified from that response), download, store."""
    from instamagic.services.posting.music import save_upload

    if source != SOURCE_OPENVERSE:
        raise NotAllowed("Unknown music source.")
    client = client or OpenverseClient()
    cand = client.detail(source_id)
    if cand.duration_s is not None and not (IMPORT_MIN_S <= cand.duration_s <= SEARCH_MAX_S):
        raise NotAllowed("Track must be between 30 seconds and 5 minutes.")
    dupe = _duplicate(session, cand)
    if dupe is not None:
        raise NotAllowed(f"Already in the library ({dupe.title}).")
    with client.stream(cand.audio_url) as resp:
        if resp.status_code >= 400:
            raise NotAllowed(f"Could not download the track (HTTP {resp.status_code}).")
        if (urlparse(str(resp.url)).scheme or "https") != "https":
            raise NotAllowed("Refusing a non-https audio download.")
        ext = _extension(cand, resp.headers.get("content-type"))
        declared = int(resp.headers.get("content-length") or 0)
        if declared > MAX_DOWNLOAD_BYTES:
            raise NotAllowed("Track is too large (max 40 MB).")
        track = save_upload(
            session,
            settings,
            f"{cand.source_id}{ext}",
            resp.iter_bytes(),
            title=cand.title,
            min_bit_rate=MIN_BIT_RATE,
            min_duration_s=IMPORT_MIN_S,
            meta={
                "artist": cand.artist,
                "source": cand.source,
                "source_id": cand.source_id,
                "source_url": cand.landing_url,
                "license": cand.license,
                "license_version": cand.license_version,
                "license_url": cand.license_url,
                "attribution": cand.attribution,
                "downloaded_at": datetime.now(UTC),
            },
        )
    if not enabled:
        track.enabled = False
        session.commit()
    log.info(
        "music_track_imported",
        track_id=str(track.id),
        source=cand.source,
        license=cand.license,
        duration_s=track.duration_s,
    )
    return track


# --- auto-refill ------------------------------------------------------------------------------


def saved_queries(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, list):
        return list(DEFAULT_QUERIES)
    out = []
    for q in data:
        q = re.sub(r"\s+", " ", str(q or "").strip())[:120]
        if q and q not in out:
            out.append(q)
    return out[:20]


def autorefill_view(session: Session) -> dict[str, object]:
    from instamagic.services.posting.schedule_config import get_or_create_config

    row = get_or_create_config(session)
    return {
        "enabled": bool(row.music_autorefill_enabled),
        "target": int(row.music_autorefill_target or 15),
        "queries": saved_queries(row.music_saved_queries_json),
        "last_run_at": row.music_autorefill_last_at,
    }


def update_autorefill(
    session: Session, *, enabled: bool | None = None, target: int | None = None, queries: list[str] | None = None
) -> dict[str, object]:
    from instamagic.services.posting.schedule_config import get_or_create_config

    row = get_or_create_config(session)
    if enabled is not None:
        row.music_autorefill_enabled = bool(enabled)
    if target is not None:
        if not 1 <= int(target) <= 100:
            raise NotAllowed("Auto-refill target must be between 1 and 100 tracks.")
        row.music_autorefill_target = int(target)
    if queries is not None:
        cleaned = saved_queries(json.dumps(list(queries)))
        if not cleaned:
            raise NotAllowed("Add at least one saved search.")
        row.music_saved_queries_json = json.dumps(cleaned)
    session.commit()
    return autorefill_view(session)


def run_autorefill(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    force: bool = False,
    client: OpenverseClient | None = None,
) -> list[MusicTrack]:
    """Weekly (when enabled): import instrumental-first matches until ``target`` tracks are enabled."""
    from instamagic.services.posting.music import enabled_tracks
    from instamagic.services.posting.schedule_config import get_or_create_config

    now = now or datetime.now(UTC)
    row = get_or_create_config(session)
    if not row.music_autorefill_enabled and not force:
        return []
    last = row.music_autorefill_last_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if not force and last is not None and now - last < AUTOREFILL_EVERY:
        return []
    row.music_autorefill_last_at = now
    session.commit()
    need = min(AUTOREFILL_MAX_PER_RUN, int(row.music_autorefill_target or 15) - len(enabled_tracks(session, settings)))
    if need <= 0:
        return []
    client = client or OpenverseClient()
    made: list[MusicTrack] = []
    for query in saved_queries(row.music_saved_queries_json):
        if len(made) >= need:
            break
        try:
            cands = client.search(query)
        except (httpx.HTTPError, NotAllowed) as exc:
            log.info("music_autorefill_search_failed", error_type=type(exc).__name__)
            continue
        cands = [
            c
            for c in cands
            if c.duration_s is not None and IMPORT_MIN_S <= c.duration_s <= SEARCH_MAX_S and not c.vocals
        ]
        cands.sort(key=lambda c: (not c.instrumental,))
        for cand in cands:
            if len(made) >= need:
                break
            if _duplicate(session, cand) is not None:
                continue
            try:
                made.append(import_track(session, settings, cand.source, cand.source_id, client=client))
                break  # one per query per run keeps the mix varied
            except (NotAllowed, NotFound, httpx.HTTPError) as exc:
                session.rollback()
                log.info("music_autorefill_import_skipped", error_type=type(exc).__name__)
    if made:
        log.info("music_autorefill_imported", count=len(made))
    return made
