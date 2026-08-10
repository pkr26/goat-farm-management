#!/usr/bin/env python3
"""Acquire a non-blocking kernel lock on an inherited backup-lock FD."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import sys


def _error(message: str) -> int:
    print(f"Cannot acquire backup lock safely: {message}", file=sys.stderr)
    return 2


def main() -> int:
    if len(sys.argv) != 3:
        return _error(f"usage: {sys.argv[0]} FD LOCK_PATH")
    try:
        lock_fd = int(sys.argv[1])
    except ValueError:
        return _error("lock file descriptor is not an integer")
    lock_path = sys.argv[2]

    try:
        opened = os.fstat(lock_fd)
        named = os.lstat(lock_path)
    except OSError as exc:
        return _error(str(exc))
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
        return _error("lock path and inherited descriptor must be the same regular file")
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        return _error("lock path changed while it was being opened")

    try:
        # flock locks the shared open-file description. The Bash parent opened
        # FD 9 before execing us, so our exit closes only the child's duplicate;
        # the lock remains held until the parent and all inherited children exit.
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 3
    except OSError as exc:
        if exc.errno == errno.EAGAIN:
            return 3
        return _error(str(exc))

    try:
        named_after_lock = os.lstat(lock_path)
        if not stat.S_ISREG(named_after_lock.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (named_after_lock.st_dev, named_after_lock.st_ino):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            return _error("lock path changed during acquisition")
        os.fchmod(lock_fd, 0o600)
    except OSError as exc:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        return _error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
