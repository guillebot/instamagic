"""Folder tree for a library, derived from ingested image paths."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from instamagic.models import Image
from instamagic.schemas.dto import FolderCrumb, FolderEntry, FolderListing
from sqlalchemy import func, select
from sqlalchemy.orm import Session

MAX_FILESYSTEM_ENTRIES = 500


def normalize_folder(raw: str | None) -> str:
    """Return a clean relative folder ('' for the root) or raise ValueError."""
    if raw is None:
        return ""
    text = raw.strip()
    if "\x00" in text or "\\" in text:
        raise ValueError("Folder path is not valid.")
    if text.startswith("/"):
        raise ValueError("Folder path must be relative to the library.")
    parts = [part for part in text.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Folder path is not valid.")
    return "/".join(parts)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _breadcrumbs(folder: str) -> list[FolderCrumb]:
    crumbs = [FolderCrumb(name="Library", path="")]
    parts = folder.split("/") if folder else []
    for index, part in enumerate(parts):
        crumbs.append(FolderCrumb(name=part, path="/".join(parts[: index + 1])))
    return crumbs


def _parent(folder: str) -> str | None:
    if not folder:
        return None
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def _filesystem_children(root: Path, folder: str) -> list[str]:
    root_resolved = root.resolve()
    target = (root_resolved / folder).resolve() if folder else root_resolved
    if os.path.commonpath([str(root_resolved), str(target)]) != str(root_resolved):
        return []
    names: list[str] = []
    try:
        with os.scandir(target) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
                if len(names) >= MAX_FILESYSTEM_ENTRIES:
                    break
    except OSError:
        return []
    return sorted(names)


def list_folders(session: Session, library_id: UUID, root: Path, folder: str) -> FolderListing:
    rows = session.execute(
        select(Image.session_name, func.count())
        .where(Image.library_id == library_id, Image.skip_reason.is_(None))
        .group_by(Image.session_name)
    ).all()
    prefix = f"{folder}/" if folder else ""
    direct = 0
    counts: dict[str, int] = {}
    sample_session: dict[str, str] = {}
    for session_name, count in rows:
        name = session_name or ""
        if name == folder:
            direct += int(count)
            continue
        if not name.startswith(prefix):
            continue
        child = name[len(prefix) :].split("/", 1)[0]
        if not child:
            continue
        counts[child] = counts.get(child, 0) + int(count)
        sample_session.setdefault(child, name)

    source = "db"
    if not counts and direct == 0:
        names = _filesystem_children(root, folder)
        if names:
            source = "filesystem"
            counts = {name: 0 for name in names}

    folders: list[FolderEntry] = []
    for child in sorted(counts, key=str.lower):
        thumbnail_url = None
        sample = sample_session.get(child)
        if sample is not None:
            image_id = session.scalar(
                select(Image.id)
                .where(
                    Image.library_id == library_id,
                    Image.session_name == sample,
                    Image.skip_reason.is_(None),
                    Image.thumbnail_path.is_not(None),
                )
                .limit(1)
            )
            if image_id is not None:
                thumbnail_url = f"/api/derivatives/{image_id}/thumbnail"
        folders.append(
            FolderEntry(
                name=child,
                path=f"{prefix}{child}",
                image_count=counts[child],
                thumbnail_url=thumbnail_url,
            )
        )
    return FolderListing(
        path=folder,
        parent=_parent(folder),
        breadcrumbs=_breadcrumbs(folder),
        folders=folders,
        image_count=direct,
        source=source,
    )
