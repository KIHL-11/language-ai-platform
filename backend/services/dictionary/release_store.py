"""Filesystem primitives for immutable dictionary releases.

Every path is resolved and constrained to ``<storage>/dictionary_releases``.
The only mutating primitives are: creating a new release directory, writing new
files inside it, sealing it read-only, and atomically replacing the
``dictionary_current`` symlink. Nothing in this module deletes user data.
"""

import json
import math
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RELEASES_DIRNAME = "dictionary_releases"
CURRENT_LINK_NAME = "dictionary_current"
NEXT_LINK_NAME = "dictionary_current.next"
DICTIONARY_NAME = "dictionary.sqlite3"
SIDECAR_NAME = "dictionary.forms.sqlite3"
LEGACY_DICTIONARY_NAME = "dictionary.sqlite3"
GIB = 1024 ** 3

_BUILD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


class ReleaseStoreError(RuntimeError):
    """A release path, link, or budget check failed closed."""


@dataclass(frozen=True, slots=True)
class ReleaseLayout:
    storage: Path
    releases: Path

    @property
    def current_link(self) -> Path:
        return self.storage / CURRENT_LINK_NAME

    @property
    def next_link(self) -> Path:
        return self.storage / NEXT_LINK_NAME

    def release(self, build_id: str) -> Path:
        return self.releases / validate_build_id(build_id)

    def current_database_path(self) -> Path:
        """Stable runtime path that always goes through the current symlink."""
        return self.current_link / DICTIONARY_NAME

    def current_sidecar_path(self) -> Path:
        return self.current_link / SIDECAR_NAME


def validate_build_id(build_id: str) -> str:
    if not isinstance(build_id, str) or not _BUILD_ID.fullmatch(build_id) or build_id in {".", ".."}:
        raise ReleaseStoreError("build ID must be a safe single path component")
    return build_id


def resolve_layout(storage_root: Path, *, create: bool = False) -> ReleaseLayout:
    """Resolve the storage root and its release directory without following escapes."""
    storage = Path(storage_root).expanduser()
    if not storage.is_dir():
        raise ReleaseStoreError("dictionary storage directory does not exist")
    storage = storage.resolve(strict=True)
    releases = storage / RELEASES_DIRNAME
    if releases.is_symlink():
        raise ReleaseStoreError("dictionary_releases must be a real directory, not a symlink")
    if not releases.exists():
        if not create:
            return ReleaseLayout(storage, releases)
        releases.mkdir(mode=0o755)
        fsync_directory(storage)
    if not releases.is_dir():
        raise ReleaseStoreError("dictionary_releases is not a directory")
    if releases.stat().st_dev != storage.stat().st_dev:
        raise ReleaseStoreError("dictionary_releases must share a filesystem with dictionary storage")
    return ReleaseLayout(storage, releases)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def existing_release(layout: ReleaseLayout, build_id: str) -> Path:
    """Return a direct, non-symlink release directory or fail closed."""
    path = layout.release(build_id)
    if path.is_symlink() or not path.is_dir():
        raise ReleaseStoreError(f"release {build_id} does not exist as a real directory")
    if path.resolve(strict=True).parent != layout.releases.resolve(strict=True):
        raise ReleaseStoreError(f"release {build_id} escapes the release root")
    return path


def create_release_directory(layout: ReleaseLayout, build_id: str) -> Path:
    """Create the final, never-renamed release directory; refuse to reuse one."""
    path = layout.release(build_id)
    if path.exists() or path.is_symlink():
        raise ReleaseStoreError(f"release {build_id} already exists; refusing to reuse it")
    path.mkdir(mode=0o755)
    fsync_directory(layout.releases)
    return path


def write_new_file_atomic(directory: Path, name: str, payload: bytes) -> bytes:
    """Write ``directory/name`` exactly once via a fsynced temporary file."""
    validate_build_id(name)
    target = directory / name
    if target.exists() or target.is_symlink():
        raise ReleaseStoreError(f"{name} already exists; published files are never rewritten")
    temporary = directory / f".{name}.partial"
    if temporary.is_symlink():
        raise ReleaseStoreError(f"{temporary.name} is an unexpected symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(temporary, target)
    fsync_directory(directory)
    return payload


def canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def seal_release(release: Path) -> None:
    """Make published files and the release directory read-only.

    ``chmod`` changes ctime, not mtime/inode, so Sidecar identity is preserved.
    """
    for child in release.iterdir():
        if child.is_symlink() or not child.is_file():
            raise ReleaseStoreError(f"unexpected non-regular entry in release: {child.name}")
        child.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    release.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    fsync_directory(release)


def current_build_id(layout: ReleaseLayout) -> str | None:
    """Return the release named by ``dictionary_current`` or ``None`` in legacy mode."""
    link = layout.current_link
    if not link.is_symlink():
        if link.exists():
            raise ReleaseStoreError("dictionary_current exists but is not a symlink")
        return None
    target = os.readlink(link)
    prefix = f"{RELEASES_DIRNAME}/"
    if not target.startswith(prefix):
        raise ReleaseStoreError("dictionary_current points outside dictionary_releases")
    build_id = validate_build_id(target[len(prefix):])
    existing_release(layout, build_id)
    return build_id


def switch_current(
    layout: ReleaseLayout,
    build_id: str,
    *,
    _before_replace: Callable[[], None] | None = None,
) -> str | None:
    """Atomically point ``dictionary_current`` at ``build_id``; return the previous ID.

    A fresh ``dictionary_current.next`` symlink is fsynced and then renamed over
    the current link, so readers observe either the old or the new release.
    """
    previous = current_build_id(layout)
    existing_release(layout, build_id)
    next_link = layout.next_link
    if next_link.is_symlink():
        # Only a symlink left by an interrupted switch can live here; it never
        # owns data, so replacing it cannot remove a release or learning file.
        next_link.unlink()
    elif next_link.exists():
        raise ReleaseStoreError("dictionary_current.next exists and is not a symlink")
    os.symlink(f"{RELEASES_DIRNAME}/{build_id}", next_link, target_is_directory=True)
    fsync_directory(layout.storage)
    if _before_replace is not None:
        _before_replace()
    os.replace(next_link, layout.current_link)
    fsync_directory(layout.storage)
    if current_build_id(layout) != build_id:
        raise ReleaseStoreError("dictionary_current does not resolve to the requested release")
    return previous


def disk_budget(
    *,
    source_bytes: int,
    dictionary_bytes: int,
    sidecar_bytes: int,
    report_bytes: int = 256 * 1024 * 1024,
    minimum_headroom_bytes: int = 10 * GIB,
) -> dict[str, int]:
    """The DQ-02 first-migration budget: R + 3D + 3S + J + Q + H."""
    values = (source_bytes, dictionary_bytes, sidecar_bytes, report_bytes, minimum_headroom_bytes)
    if any(type(value) is not int or value < 0 for value in values):
        raise ReleaseStoreError("disk budget inputs must be non-negative integers")
    journal = math.ceil(0.25 * dictionary_bytes)
    working = source_bytes + 3 * dictionary_bytes + 3 * sidecar_bytes + journal + report_bytes
    headroom = max(minimum_headroom_bytes, math.ceil(0.25 * working))
    return {
        "R_source_bytes": source_bytes,
        "D_dictionary_bytes": dictionary_bytes,
        "S_sidecar_bytes": sidecar_bytes,
        "J_journal_bytes": journal,
        "Q_report_bytes": report_bytes,
        "H_headroom_bytes": headroom,
        "required_free_bytes": working + headroom,
    }


def require_free_space(
    path: Path,
    required: int,
    *,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> int:
    free = int(disk_usage(path).free)
    if free < required:
        raise ReleaseStoreError(
            f"insufficient disk space: {free} bytes free, {required} bytes required"
        )
    return free
