from __future__ import annotations

import os
from pathlib import Path

from instamagic.errors import NotFound


def safe_join(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\x00" in relative:
        raise NotFound("Derivative was not found.")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if os.path.commonpath([str(root_resolved), str(candidate)]) != str(root_resolved):
        raise NotFound("Derivative was not found.")
    return candidate


def assert_library_root(path: Path, allowed_roots: list[Path]) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("Library path is not a directory.")
    if not allowed_roots:
        raise ValueError("No allowed library roots are configured.")
    for root in allowed_roots:
        root_resolved = root.expanduser().resolve()
        if resolved == root_resolved or str(resolved).startswith(str(root_resolved) + os.sep):
            return resolved
    raise ValueError("Library path is outside the allowed roots.")
