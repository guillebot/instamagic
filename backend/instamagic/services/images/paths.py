from __future__ import annotations

import errno
import os
import time
from collections.abc import Callable
from pathlib import Path

from instamagic.errors import NotFound

EXTERNAL_MOUNT_HINT = (
    "Docker often creates an empty directory at the mount point when the host bind is missing. "
    "Set ORIGINALS_ROOT to an existing host folder and recreate the containers "
    "(docker compose up -d --force-recreate)."
)

# External USB/NAS mounts often return these transiently under load or sleep/wake.
_TRANSIENT_ERRNOS = {
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    errno.EBUSY,
    errno.EINTR,
    errno.ETIMEDOUT,
}
if hasattr(errno, "ESTALE"):
    _TRANSIENT_ERRNOS.add(errno.ESTALE)


def is_transient_io_error(exc: BaseException) -> bool:
    if isinstance(exc, (BlockingIOError, TimeoutError, InterruptedError)):
        return True
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNOS:
        return True
    return False


def retry_transient_io[T](
    fn: Callable[[], T],
    *,
    attempts: int = 6,
    base_delay: float = 0.2,
) -> T:
    """Retry filesystem ops that fail with EAGAIN/EBUSY on flaky external mounts."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    for index in range(attempts):
        try:
            return fn()
        except OSError as exc:
            last = exc
            if not is_transient_io_error(exc) or index >= attempts - 1:
                raise
            time.sleep(base_delay * (2**index))
    assert last is not None
    raise last


def safe_join(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\x00" in relative:
        raise NotFound("Derivative was not found.")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if os.path.commonpath([str(root_resolved), str(candidate)]) != str(root_resolved):
        raise NotFound("Derivative was not found.")
    return candidate


def is_mount_point(path: Path) -> bool:
    """True when path is a filesystem mount (bind mount, volume, etc.)."""
    resolved = path.expanduser().resolve()
    if os.path.ismount(resolved):
        return True
    try:
        parent = resolved.parent
        if parent == resolved:
            return True
        return os.stat(resolved).st_dev != os.stat(parent).st_dev
    except OSError:
        return False


def assert_external_mount(path: Path | str, *, label: str = "Library path") -> Path:
    """Fail closed when an expected host bind mount is missing inside the container."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"{label} does not exist ({resolved}). {EXTERNAL_MOUNT_HINT}")
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory ({resolved}).")
    if not is_mount_point(resolved):
        raise ValueError(f"{label} is not an external mount ({resolved}). {EXTERNAL_MOUNT_HINT}")
    return resolved


def assert_configured_external_mounts(roots: list[Path]) -> None:
    if not roots:
        raise ValueError(
            "REQUIRE_EXTERNAL_LIBRARY_MOUNTS is set but ALLOWED_LIBRARY_ROOTS is empty."
        )
    for root in roots:
        assert_external_mount(root, label="Allowed library root")


def assert_library_root(path: Path | str, allowed_roots: list[Path]) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("Library path is not a directory.")
    if not allowed_roots:
        raise ValueError("No allowed library roots are configured.")
    for root in allowed_roots:
        root_resolved = root.expanduser().resolve()
        if resolved == root_resolved or str(resolved).startswith(str(root_resolved) + os.sep):
            return resolved
    raise ValueError("Library path is outside the allowed roots.")


def assert_library_root_mounted(path: Path | str, allowed_roots: list[Path]) -> Path:
    """Validate path is under an allowed root and that root is a real external mount."""
    resolved = assert_library_root(path, allowed_roots)
    for root in allowed_roots:
        root_resolved = root.expanduser().resolve()
        if resolved == root_resolved or str(resolved).startswith(str(root_resolved) + os.sep):
            assert_external_mount(root_resolved, label="Library mount")
            return resolved
    raise ValueError("Library path is outside the allowed roots.")
