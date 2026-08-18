#!/usr/bin/env python3
"""Copy one stable regular-file snapshot without reopening its pathname."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_COPY_CHUNK_BYTES = 1024 * 1024


class PinnedCopyError(RuntimeError):
    """The source could not be copied without a pathname/content race."""


def _write_all(file_descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(file_descriptor, remaining)
        if written == 0:
            raise PinnedCopyError("destination write made no progress")
        remaining = remaining[written:]


def copy_pinned_regular_file(source: Path, destination: Path) -> None:
    """Copy exactly the regular file opened at ``source`` into a new file.

    Reading only the size observed by the first ``fstat`` bounds a source that
    is concurrently appended forever. A final one-byte probe and metadata
    comparison reject growth, truncation, or in-place mutation. Pathname
    replacement cannot redirect the already-open descriptor.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise PinnedCopyError("safe no-follow/nonblocking file open is unavailable")

    source_fd = -1
    destination_fd = -1
    destination_created = False
    completed = False
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise PinnedCopyError("source is not a regular file")

        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        destination_created = True
        remaining = before.st_size
        while remaining:
            chunk = os.read(source_fd, min(_COPY_CHUNK_BYTES, remaining))
            if not chunk:
                raise PinnedCopyError("source was truncated while it was copied")
            _write_all(destination_fd, chunk)
            remaining -= len(chunk)

        if os.read(source_fd, 1):
            raise PinnedCopyError("source grew while it was copied")
        after = os.fstat(source_fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ) or (before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PinnedCopyError("source changed while it was copied")
        os.fsync(destination_fd)
        completed = True
    except OSError as exc:
        raise PinnedCopyError(str(exc)) from exc
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)
        if destination_created and not completed:
            try:
                destination.unlink()
            except OSError:
                pass


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} SOURCE DESTINATION", file=sys.stderr)
        return 2
    destination = Path(sys.argv[2])
    try:
        copy_pinned_regular_file(Path(sys.argv[1]), destination)
    except PinnedCopyError as exc:
        print(f"Cannot copy a stable regular-file snapshot: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
