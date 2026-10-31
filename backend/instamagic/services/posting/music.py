"""Reel music library: operator-uploaded royalty-free / licensed tracks baked into Reel MP4s.

Files live under ``settings.music_root`` (a host folder bind-mounted into the backend and the
render worker). Nothing here downloads music: the operator uploads tracks they have the rights
to (e.g. YouTube Audio Library, Pixabay Music, Epidemic Sound). Copyrighted songs can get a Reel
muted or blocked by Instagram.

Per-Reel choice (``PublishingQueueItem.music_choice``): None = auto (rotation or random over the
enabled tracks, per the schedule setting), "none" = silent, otherwise a track id. With an empty
library the Reel renders silent and the Queue shows a warning badge.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterable
from pathlib import Path

from instamagic.config import Settings
from instamagic.errors import NotAllowed, NotFound
from instamagic.logging_config import get_logger
from instamagic.models import MusicTrack, PublishingQueueItem
from sqlalchemy import select
from sqlalchemy.orm import Session

log = get_logger(__name__)

ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MIN_DURATION_S = 3.0
MAX_DURATION_S = 20 * 60.0
MUSIC_NONE = "none"
LICENSE_NOTE = (
    "Upload only royalty-free or licensed tracks (YouTube Audio Library, Pixabay Music, Epidemic Sound). "
    "Copyrighted songs can get a Reel muted or blocked."
)
SILENT_EMPTY = "library_empty"
SILENT_CHOSEN = "silent_chosen"
SILENT_MISSING = "track_missing"


def music_root(settings: Settings) -> Path:
    return Path(settings.music_root)


def _sanitize(text: str) -> str:
    text = re.sub(r"[\x00-\x1f]+", " ", text or "")
    text = re.sub(r"\s+", " ", text.replace("_", " ")).strip()
    return (text or "Untitled track")[:200]


def _clean_title(filename: str) -> str:
    return _sanitize(Path(filename or "track").stem)


def track_path(settings: Settings, track: MusicTrack) -> Path:
    root = music_root(settings).resolve()
    path = (root / track.relative_path).resolve()
    if root not in path.parents:
        raise NotAllowed("Music track path is outside the music library.")
    return path


def probe_audio(path: Path) -> float:
    """Duration (s) of the first audio stream; raises NotAllowed if the file has no audio."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise NotAllowed("ffprobe is not installed; cannot check uploaded music.")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        info = json.loads(out.stdout or b"{}")
    except (subprocess.SubprocessError, ValueError) as exc:
        raise NotAllowed("That file is not a readable audio track.") from exc
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise NotAllowed("That file has no audio stream.")
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration < MIN_DURATION_S:
        raise NotAllowed("Track is too short (needs at least 3 seconds).")
    if duration > MAX_DURATION_S:
        raise NotAllowed("Track is too long (max 20 minutes).")
    return round(duration, 3)


def save_upload(session: Session, settings: Settings, filename: str, chunks: Iterable[bytes]) -> MusicTrack:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise NotAllowed("Upload an mp3, m4a, aac, wav, ogg or flac file.")
    root = music_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    track_id = uuid.uuid4()
    tmp = root / f".incoming-{track_id.hex}{ext}"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise NotAllowed("Track is too large (max 40 MB).")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise NotAllowed("Empty upload.")
        duration = probe_audio(tmp)
        sha = digest.hexdigest()
        duplicate = session.scalars(select(MusicTrack).where(MusicTrack.sha256 == sha)).first()
        if duplicate is not None:
            raise NotAllowed(f"That track is already in the library ({duplicate.title}).")
        relative = f"{track_id.hex}{ext}"
        os.replace(tmp, root / relative)
    finally:
        tmp.unlink(missing_ok=True)
    track = MusicTrack(
        id=track_id,
        title=_clean_title(filename),
        relative_path=relative,
        sha256=sha,
        size_bytes=size,
        duration_s=duration,
        enabled=True,
    )
    session.add(track)
    session.commit()
    session.refresh(track)
    log.info("music_track_uploaded", track_id=str(track.id), size_bytes=size, duration_s=duration)
    return track


def list_tracks(session: Session) -> list[MusicTrack]:
    return list(session.scalars(select(MusicTrack).order_by(MusicTrack.created_at.desc())).all())


def get_track(session: Session, track_id) -> MusicTrack:
    try:
        key = track_id if isinstance(track_id, uuid.UUID) else uuid.UUID(str(track_id))
    except ValueError as exc:
        raise NotFound("Music track was not found.") from exc
    track = session.get(MusicTrack, key)
    if track is None:
        raise NotFound("Music track was not found.")
    return track


def update_track(session: Session, track_id, *, enabled: bool | None = None, title: str | None = None) -> MusicTrack:
    track = get_track(session, track_id)
    if enabled is not None:
        track.enabled = bool(enabled)
    if title is not None and title.strip():
        track.title = _sanitize(title)
    session.commit()
    session.refresh(track)
    return track


def delete_track(session: Session, settings: Settings, track_id) -> None:
    track = get_track(session, track_id)
    path = track_path(settings, track)
    key = str(track.id)
    # Reels that explicitly chose this track fall back to auto (they re-render on approval).
    for item in session.scalars(select(PublishingQueueItem).where(PublishingQueueItem.music_choice == key)).all():
        item.music_choice = None
    session.delete(track)
    session.commit()
    path.unlink(missing_ok=True)
    log.info("music_track_deleted", track_id=key)


def enabled_tracks(session: Session, settings: Settings) -> list[MusicTrack]:
    rows = session.scalars(select(MusicTrack).where(MusicTrack.enabled.is_(True))).all()
    return [row for row in rows if track_path(settings, row).is_file()]


def pick_track(session: Session, settings: Settings, mode: str, *, rng: random.Random | None = None) -> MusicTrack | None:
    tracks = enabled_tracks(session, settings)
    if not tracks:
        return None
    if mode == "random":
        return (rng or random).choice(tracks)
    # Rotation: least recently used first (never-used first, then oldest upload).
    return min(
        tracks,
        key=lambda t: (t.last_used_at is not None, t.last_used_at or t.created_at, t.created_at),
    )


def _recorded(item: PublishingQueueItem) -> dict:
    try:
        payload = json.loads(item.reel_source_json or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_reel_music(
    session: Session, settings: Settings, item: PublishingQueueItem
) -> tuple[MusicTrack | None, Path | None, str | None]:
    """(track, file, silent_reason) for this Reel's render."""
    from instamagic.services.posting.schedule_config import config_view

    choice = (item.music_choice or "").strip()
    if choice == MUSIC_NONE:
        return None, None, SILENT_CHOSEN
    if choice:
        try:
            track = get_track(session, choice)
        except NotFound:
            track = None
        if track is None or not track_path(settings, track).is_file():
            return None, None, SILENT_MISSING
        return track, track_path(settings, track), None
    # Auto: keep the previously rendered track when it is still available (stable re-renders).
    previous = (_recorded(item).get("music") or {}).get("id")
    if previous:
        try:
            track = get_track(session, previous)
            if track.enabled and track_path(settings, track).is_file():
                return track, track_path(settings, track), None
        except NotFound:
            pass
    track = pick_track(session, settings, config_view(session).music_pick_mode)
    if track is None:
        return None, None, SILENT_EMPTY
    return track, track_path(settings, track), None


def music_choice_matches(item: PublishingQueueItem, recorded_music: dict | None) -> bool:
    """Does the rendered MP4's audio still match the Reel's music choice?"""
    choice = (item.music_choice or "").strip()
    if not choice:
        return True
    if choice == MUSIC_NONE:
        return recorded_music is None
    return bool(recorded_music) and str(recorded_music.get("id")) == choice


SILENT_WARNINGS = {
    SILENT_EMPTY: "No music in the library: this Reel is silent. Upload royalty-free tracks under Music.",
    SILENT_MISSING: "The chosen track is gone: this Reel is silent. Pick another track and render again.",
}


def reel_music_out(item: PublishingQueueItem) -> dict[str, object] | None:
    """What the rendered Reel carries; None before the first render.

    ``{"track": {"id", "title"} | None, "silent": bool, "reason": str | None, "warning": str | None}``
    """
    if not item.reel_source_json:
        return None
    recorded = _recorded(item)
    music = recorded.get("music")
    if music:
        return {
            "track": {"id": music.get("id"), "title": music.get("title")},
            "silent": False,
            "reason": None,
            "warning": None,
        }
    reason = recorded.get("music_note") or SILENT_EMPTY
    return {"track": None, "silent": True, "reason": reason, "warning": SILENT_WARNINGS.get(reason)}


def track_out(track: MusicTrack) -> dict[str, object]:
    return {
        "id": track.id,
        "title": track.title,
        "duration_s": track.duration_s,
        "size_bytes": track.size_bytes,
        "enabled": bool(track.enabled),
        "last_used_at": track.last_used_at,
        "created_at": track.created_at,
        "preview_url": f"/api/music/{track.id}/audio",
    }
