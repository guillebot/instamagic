"""Instagram collaborator handles per queue item, remembered per shoot.

A queue item can carry up to 3 collaborator usernames (the model, a stylist, ...). They are sent
as the Graph ``collaborators`` parameter for feed images, carousels (parent container) and Reels;
Stories do not support collaborators and never receive them. Instagram sends each collaborator an
invite; the post shows on their profile only after they accept.

Handles are remembered per shoot (``Image.session_name``, falling back to the photo's folder) so
the next photo from the same shoot is pre-filled. Nothing here publishes or contacts anyone.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from posixpath import dirname

from instamagic.errors import NotAllowed
from instamagic.models import Image, PublishingQueueItem, ShootCollaborators
from instamagic.models.enums import QueueMediaType
from sqlalchemy.orm import Session

MAX_COLLABORATORS = 3
# Instagram usernames: 1-30 chars, letters/digits/period/underscore, no leading/trailing period,
# no consecutive periods.
_HANDLE = re.compile(r"^(?!\.)(?!.*\.\.)(?!.*\.$)[a-z0-9._]{1,30}$")
COLLABORATOR_MEDIA_TYPES = {
    QueueMediaType.IMAGE.value,
    QueueMediaType.CAROUSEL.value,
    QueueMediaType.REELS.value,
}


def _clean(handle: str) -> str:
    value = (handle or "").strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I).strip("/")
    return value.lstrip("@").strip().lower()


def validate_collaborators(handles: list[str]) -> list[str]:
    """Strict: raise NotAllowed on any invalid handle or more than 3."""
    out: list[str] = []
    for raw in handles:
        value = _clean(raw)
        if not value:
            continue
        if not _HANDLE.match(value):
            raise NotAllowed(f"'{raw}' is not a valid Instagram username.")
        if value not in out:
            out.append(value)
    if len(out) > MAX_COLLABORATORS:
        raise NotAllowed(f"Instagram allows at most {MAX_COLLABORATORS} collaborators per post.")
    return out


def normalize_collaborators(handles: list[str]) -> list[str]:
    """Lenient (publish time): keep only valid, unique handles, at most 3."""
    out: list[str] = []
    for raw in handles:
        value = _clean(str(raw))
        if value and _HANDLE.match(value) and value not in out:
            out.append(value)
    return out[:MAX_COLLABORATORS]


def parse_collaborators(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return normalize_collaborators([str(x) for x in data])


def shoot_key(image: Image | None) -> str | None:
    if image is None:
        return None
    session_name = (image.session_name or "").strip()
    if session_name:
        return f"session:{session_name}"[:500]
    folder = dirname(image.relative_path or "")
    if folder:
        return f"folder:{image.library_id}:{folder}"[:500]
    return None


def remembered_for_image(session: Session, image: Image | None) -> list[str]:
    key = shoot_key(image)
    if not key:
        return []
    row = session.get(ShootCollaborators, key)
    return parse_collaborators(row.collaborators_json) if row else []


def remember_for_image(session: Session, image: Image | None, handles: list[str]) -> None:
    key = shoot_key(image)
    if not key:
        return
    row = session.get(ShootCollaborators, key)
    payload = json.dumps(handles)
    if row is None:
        session.add(ShootCollaborators(shoot_key=key, collaborators_json=payload, updated_at=datetime.now(UTC)))
    else:
        row.collaborators_json = payload
        row.updated_at = datetime.now(UTC)


def item_collaborators(item: PublishingQueueItem) -> list[str]:
    """What is actually sent for this item (empty for Stories)."""
    if item.media_type not in COLLABORATOR_MEDIA_TYPES:
        return []
    return parse_collaborators(item.collaborators_json)


def prefill_from_shoot(session: Session, item: PublishingQueueItem, image: Image | None) -> None:
    """New queue items inherit the shoot's remembered handles (visible + editable in the Queue)."""
    if item.collaborators_json is not None or item.media_type not in COLLABORATOR_MEDIA_TYPES:
        return
    handles = remembered_for_image(session, image)
    if handles:
        item.collaborators_json = json.dumps(handles)


def set_item_collaborators(session: Session, item: PublishingQueueItem, handles: list[str]) -> list[str]:
    from instamagic.models import QueueState

    if item.media_type not in COLLABORATOR_MEDIA_TYPES:
        raise NotAllowed("Stories can't have collaborators (Instagram API limit).")
    if item.state in {QueueState.PUBLISHING.value, QueueState.PUBLISHED.value}:
        raise NotAllowed("Collaborators can't change once publishing started.")
    clean = validate_collaborators(handles)
    item.collaborators_json = json.dumps(clean)
    image = session.get(Image, item.image_id) if item.image_id else None
    remember_for_image(session, image, clean)
    return clean
