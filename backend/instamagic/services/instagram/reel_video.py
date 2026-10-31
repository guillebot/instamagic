"""Render vertical 1080x1920 Reel MP4s from verified 9:16 story frames (ffmpeg).

Audio: an operator-uploaded music track (trimmed to the video, faded in/out, loudness-normalized)
or a silent AAC track when no music is chosen / the library is empty.

Every frame is a crop/zoom of a 9:16 composition built *only* from the verified feed publish
derivative (see ``story_image``), so a Reel can never contain sharper pixels than the feed post
that passed the blur gates. Motion = slow Ken Burns zoom; multiple photos = crossfade slideshow.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from instamagic.errors import NotConfigured
from PIL import Image

REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_FPS = 30
REEL_MIN_SECONDS = 7.0
REEL_MAX_SECONDS = 15.0
CROSSFADE_SECONDS = 0.5
KEN_BURNS_ZOOM = 1.08
_SINGLE_SECONDS = 8.0
_PER_SLIDE_SECONDS = 3.0


@dataclass(frozen=True)
class ReelRender:
    duration_s: float
    sha256: str
    width: int
    height: int
    video_codec: str
    audio_codec: str | None


def ffmpeg_binary() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise NotConfigured("ffmpeg is not installed in this container; cannot render reels.")
    return path


def plan_durations(count: int) -> tuple[float, float]:
    """(per-slide seconds, total seconds) clamped to Instagram-friendly 7-15s."""
    if count <= 0:
        raise NotConfigured("A reel needs at least one photo.")
    if count == 1:
        return _SINGLE_SECONDS, _SINGLE_SECONDS
    per = _PER_SLIDE_SECONDS
    total = count * per - (count - 1) * CROSSFADE_SECONDS
    if total < REEL_MIN_SECONDS:
        per = (REEL_MIN_SECONDS + (count - 1) * CROSSFADE_SECONDS) / count
    elif total > REEL_MAX_SECONDS:
        per = (REEL_MAX_SECONDS + (count - 1) * CROSSFADE_SECONDS) / count
    total = count * per - (count - 1) * CROSSFADE_SECONDS
    return per, total


def _zoom_frame(base: Image.Image, progress: float, index: int) -> Image.Image:
    """Ken Burns: zoom from 1.0 to KEN_BURNS_ZOOM with a gentle alternating vertical drift."""
    zoom = 1.0 + (KEN_BURNS_ZOOM - 1.0) * progress
    w = REEL_WIDTH / zoom
    h = REEL_HEIGHT / zoom
    spare_x = REEL_WIDTH - w
    spare_y = REEL_HEIGHT - h
    drift = progress if index % 2 == 0 else 1.0 - progress
    x0 = spare_x / 2
    y0 = spare_y * (0.35 + 0.3 * drift)
    return base.resize((REEL_WIDTH, REEL_HEIGHT), Image.Resampling.BILINEAR, box=(x0, y0, x0 + w, y0 + h))


def iter_frames(frames: list[Image.Image]) -> Iterator[bytes]:
    per, total = plan_durations(len(frames))
    bases = [frame.convert("RGB") for frame in frames]
    if any(base.size != (REEL_WIDTH, REEL_HEIGHT) for base in bases):
        raise NotConfigured("Reel frames must be 1080x1920 story renders.")
    step = per - CROSSFADE_SECONDS if len(bases) > 1 else per
    n_frames = int(round(total * REEL_FPS))
    for f in range(n_frames):
        t = f / REEL_FPS
        idx = min(int(t // step), len(bases) - 1)
        local = t - idx * step
        current = _zoom_frame(bases[idx], min(1.0, local / per), idx)
        if idx > 0 and local < CROSSFADE_SECONDS:
            # Crossfade: previous slide finishing its zoom, blended into this one.
            previous = _zoom_frame(bases[idx - 1], min(1.0, (local + step) / per), idx - 1)
            current = Image.blend(previous, current, local / CROSSFADE_SECONDS)
        yield current.tobytes()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise NotConfigured("ffprobe is not installed in this container.")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return json.loads(out.stdout or b"{}")


MUSIC_FADE_IN_SECONDS = 0.8
MUSIC_FADE_OUT_SECONDS = 1.5
# Integrated loudness target for mobile playback (Instagram/most platforms normalize near -14 LUFS).
MUSIC_LOUDNESS = "I=-14:TP=-1.5:LRA=11"


def music_filter(total: float) -> str:
    """Trim to the video length, normalize loudness, then fade in/out (48 kHz stereo)."""
    fade_out = max(0.0, total - MUSIC_FADE_OUT_SECONDS)
    return (
        f"[1:a]atrim=0:{total:.3f},asetpts=N/SR/TB,loudnorm={MUSIC_LOUDNESS},"
        "aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:st=0:d={MUSIC_FADE_IN_SECONDS},"
        f"afade=t=out:st={fade_out:.3f}:d={MUSIC_FADE_OUT_SECONDS}[aout]"
    )


def _audio_args(music: Path | None, total: float) -> list[str]:
    if music is None:
        return [
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
        ]
    # Loop short tracks so the audio always covers the whole video.
    return [
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", music_filter(total),
        "-map", "0:v:0", "-map", "[aout]",
    ]


def write_reel_mp4(frames: list[Image.Image], dest: Path, *, music: Path | None = None) -> ReelRender:
    """Encode frames to an Instagram-compatible MP4 (H.264 High, yuv420p, 30fps, AAC music or silence)."""
    if not 1 <= len(frames) <= 10:
        raise NotConfigured("A reel needs between 1 and 10 photos.")
    binary = ffmpeg_binary()
    _per, total = plan_durations(len(frames))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".partial.mp4")
    cmd = [
        binary, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{REEL_WIDTH}x{REEL_HEIGHT}", "-r", str(REEL_FPS), "-i", "-",
        *_audio_args(music, total),
        "-t", f"{total:.3f}", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
        "-g", str(REEL_FPS * 2), "-keyint_min", str(REEL_FPS), "-sc_threshold", "0", "-r", str(REEL_FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(tmp),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for chunk in iter_frames(frames):
            proc.stdin.write(chunk)
        proc.stdin.close()
        assert proc.stderr is not None
        stderr = proc.stderr.read()  # -loglevel error keeps this tiny
        proc.wait(timeout=300)
    except BaseException:
        proc.kill()
        proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        detail = (stderr or b"").decode("utf-8", "replace").strip()[-300:]
        raise NotConfigured(f"ffmpeg failed to encode the reel: {detail or proc.returncode}")
    info = probe_video(tmp)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)  # type: ignore[union-attr]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)  # type: ignore[union-attr]
    if not video or int(video.get("width", 0)) != REEL_WIDTH or int(video.get("height", 0)) != REEL_HEIGHT:
        tmp.unlink(missing_ok=True)
        raise NotConfigured("Rendered reel is not 1080x1920.")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        tmp.unlink(missing_ok=True)
        raise NotConfigured("Rendered reel is not H.264 yuv420p.")
    duration = float((info.get("format") or {}).get("duration") or total)  # type: ignore[union-attr]
    if not (3.0 <= duration <= 90.0):
        tmp.unlink(missing_ok=True)
        raise NotConfigured("Rendered reel duration is outside Instagram's limits.")
    os.replace(tmp, dest)
    return ReelRender(
        duration_s=round(duration, 3),
        sha256=_sha256(dest),
        width=REEL_WIDTH,
        height=REEL_HEIGHT,
        video_codec=str(video.get("codec_name")),
        audio_codec=str(audio.get("codec_name")) if audio else None,
    )


def file_sha256(path: Path) -> str:
    return _sha256(path)
