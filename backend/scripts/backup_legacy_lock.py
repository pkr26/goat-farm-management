#!/usr/bin/env python3
"""Safely identify, publish, move, and remove legacy backup locks."""

from __future__ import annotations

import base64
import ctypes
import errno
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_MAX_LOCK_FILE_BYTES = 128
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


class LegacyLockError(RuntimeError):
    """The legacy lock could not be handled without an unsafe assumption."""


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    content: bytes


@dataclass(frozen=True)
class LockSnapshot:
    directory_device: int
    directory_inode: int
    pid_file: FileSnapshot
    owner_file: FileSnapshot | None

    @property
    def pid(self) -> int:
        return int(self.pid_file.content[:-1])

    @property
    def lock_format(self) -> str:
        if self.owner_file is None:
            return "old"
        expected = rb"%d-[0-9]{1,5}-[0-9]{1,5}\n" % self.pid
        if re.fullmatch(expected, self.owner_file.content) is not None:
            return "new"
        return "untrusted"


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_file(file_fd: int, description: str) -> bytes:
    content = bytearray()
    while len(content) <= _MAX_LOCK_FILE_BYTES:
        chunk = os.read(file_fd, _MAX_LOCK_FILE_BYTES + 1 - len(content))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) > _MAX_LOCK_FILE_BYTES:
        raise LegacyLockError(f"{description} is too large")
    return bytes(content)


def _snapshot_file(directory_fd: int, name: str, description: str) -> FileSnapshot:
    # O_NONBLOCK is inert for regular files and prevents a malicious/malformed
    # FIFO entry from hanging the safety helper before fstat can reject it.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    file_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise LegacyLockError(f"{description} is not a regular file")
        content = _read_file(file_fd, description)
        after = os.fstat(file_fd)
    finally:
        os.close(file_fd)
    named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not _same_inode(before, after)
        or not _same_inode(before, named)
        or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise LegacyLockError(f"{description} changed while it was read")
    return FileSnapshot(before.st_dev, before.st_ino, content)


def snapshot(lock_path: Path) -> LockSnapshot:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open(lock_path, directory_flags)
    try:
        directory_before = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_before.st_mode):
            raise LegacyLockError("legacy lock is not a directory")

        entries_before = set(os.listdir(directory_fd))
        if entries_before not in ({"pid"}, {"pid", ".flock-owner"}):
            raise LegacyLockError("legacy lock contains unexpected directory entries")
        pid_file = _snapshot_file(directory_fd, "pid", "legacy PID file")
        if re.fullmatch(rb"[1-9][0-9]*\n", pid_file.content) is None:
            raise LegacyLockError("legacy PID file must contain exactly one positive PID")
        owner_file = None
        if ".flock-owner" in entries_before:
            owner_file = _snapshot_file(
                directory_fd,
                ".flock-owner",
                "legacy ownership file",
            )
            if re.fullmatch(rb"[^\x00\r\n]+\n", owner_file.content) is None:
                raise LegacyLockError("legacy ownership file must contain exactly one token")

        # Re-resolve the directory name and entry set while the opened inode is
        # pinned. A replacement during snapshot cannot masquerade as the files
        # whose content and identity were read above.
        named_directory = os.lstat(lock_path)
        directory_after = os.fstat(directory_fd)
        entries_after = set(os.listdir(directory_fd))
        if (
            not stat.S_ISDIR(named_directory.st_mode)
            or not _same_inode(directory_before, directory_after)
            or not _same_inode(directory_before, named_directory)
            or entries_before != entries_after
        ):
            raise LegacyLockError("legacy lock identity changed during snapshot")

        return LockSnapshot(
            directory_device=directory_before.st_dev,
            directory_inode=directory_before.st_ino,
            pid_file=pid_file,
            owner_file=owner_file,
        )
    finally:
        os.close(directory_fd)


def encode_snapshot(value: LockSnapshot) -> str:
    owner_payload: list[int | str] | None = None
    if value.owner_file is not None:
        owner_payload = [
            value.owner_file.device,
            value.owner_file.inode,
            base64.b64encode(value.owner_file.content).decode("ascii"),
        ]
    payload = [
        value.directory_device,
        value.directory_inode,
        value.pid_file.device,
        value.pid_file.inode,
        base64.b64encode(value.pid_file.content).decode("ascii"),
        owner_payload,
    ]
    serialized = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(serialized).decode("ascii")


def _decode_file(payload: object, description: str) -> FileSnapshot:
    if (
        not isinstance(payload, list)
        or len(payload) != 3
        or type(payload[0]) is not int
        or type(payload[1]) is not int
        or not isinstance(payload[2], str)
    ):
        raise LegacyLockError(f"invalid {description} snapshot fields")
    try:
        content = base64.b64decode(payload[2], validate=True)
    except ValueError as exc:
        raise LegacyLockError(f"invalid {description} snapshot content") from exc
    return FileSnapshot(payload[0], payload[1], content)


def decode_snapshot(token: str) -> LockSnapshot:
    try:
        serialized = base64.b64decode(token, altchars=b"-_", validate=True)
        payload = json.loads(serialized)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise LegacyLockError("invalid legacy lock snapshot token") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 6
        or type(payload[0]) is not int
        or type(payload[1]) is not int
    ):
        raise LegacyLockError("invalid legacy lock snapshot fields")
    pid_file = _decode_file(payload[2:5], "PID file")
    if re.fullmatch(rb"[1-9][0-9]*\n", pid_file.content) is None:
        raise LegacyLockError("invalid PID content in legacy lock snapshot")
    owner_file = None
    if payload[5] is not None:
        owner_file = _decode_file(payload[5], "ownership file")
    return LockSnapshot(payload[0], payload[1], pid_file, owner_file)


def pid_status(pid: int) -> str:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        # EPERM proves that the process exists even though this account cannot
        # signal it. Treating that as stale would violate the transition lock.
        return "live"
    except OSError as exc:
        raise LegacyLockError(f"cannot determine whether PID {pid} is live: {exc}") from exc
    return "live"


def _rename_noreplace_darwin(source: bytes, destination: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = libc.renamex_np
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    if renamex_np(source, destination, _RENAME_EXCL) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _rename_noreplace_linux(source: bytes, destination: bytes) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            _AT_FDCWD,
            source,
            _AT_FDCWD,
            destination,
            _RENAME_NOREPLACE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _platform_rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename ``source`` only when ``destination`` is absent.

    There is deliberately no check-then-rename fallback.  For directories,
    plain ``rename`` may replace a destination that another (legacy) producer
    created empty just after the existence check.  That producer can then
    populate the replacement directory after our post-move snapshot, leaving
    both processes believing they own the backup lock.  Filesystems without
    the required atomic primitive must fail closed instead of weakening the
    serialization guarantee.
    """
    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    if sys.platform == "darwin":
        _rename_noreplace_darwin(encoded_source, encoded_destination)
    elif sys.platform.startswith("linux"):
        _rename_noreplace_linux(encoded_source, encoded_destination)
    else:
        raise OSError(errno.ENOSYS, "atomic exclusive directory rename is unavailable")


def _restore_after_mismatch(source: Path, destination: Path) -> str:
    """Put a moved unexpected lock back, but never overwrite a new claimant."""
    try:
        _platform_rename_noreplace(destination, source)
    except OSError as exc:
        try:
            os.lstat(source)
        except FileNotFoundError as absent_exc:
            raise LegacyLockError(
                "unexpected lock was moved and canonical name is absent; "
                f"exclusive restoration failed: {exc}"
            ) from absent_exc
        return f"canonical name is occupied; preserved unexpected lock at {destination}"
    return "restored unexpected lock to the unoccupied canonical name"


def move_verified(source: Path, destination: Path, token: str) -> None:
    """Exclusively move the exact snapshot, restoring any raced replacement."""
    expected = decode_snapshot(token)
    source_now = snapshot(source)
    if source_now != expected:
        raise LegacyLockError("canonical lock changed before quarantine and was not moved")

    # A replacement can still occur after the snapshot's final lstat and before
    # this syscall. Post-move identity verification is mandatory; on mismatch,
    # restore the object without overwriting any claimant that won the gap.
    _platform_rename_noreplace(source, destination)
    try:
        moved = snapshot(destination)
    except (LegacyLockError, OSError) as exc:
        outcome = _restore_after_mismatch(source, destination)
        raise LegacyLockError(f"moved lock could not be verified; {outcome}") from exc
    if moved != expected:
        outcome = _restore_after_mismatch(source, destination)
        raise LegacyLockError(f"moved lock did not match verified snapshot; {outcome}")


def delete_verified(lock_path: Path, token: str) -> None:
    expected = decode_snapshot(token)
    directory_fd = os.open(
        lock_path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        named = os.lstat(lock_path)
        opened = os.fstat(directory_fd)
        entries = set(os.listdir(directory_fd))
        pid_file = _snapshot_file(directory_fd, "pid", "owned PID file")
        owner_file = None
        if ".flock-owner" in entries:
            owner_file = _snapshot_file(
                directory_fd,
                ".flock-owner",
                "owned ownership file",
            )
        found = LockSnapshot(opened.st_dev, opened.st_ino, pid_file, owner_file)
        if (
            found != expected
            or entries not in ({"pid"}, {"pid", ".flock-owner"})
            or not _same_inode(opened, named)
        ):
            raise LegacyLockError("owned lock changed immediately before deletion")
        if expected.owner_file is not None:
            os.unlink(".flock-owner", dir_fd=directory_fd)
        os.unlink("pid", dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    os.rmdir(lock_path)


def release_verified(source: Path, destination: Path, token: str) -> None:
    expected = decode_snapshot(token)
    if expected.lock_format != "new":
        raise LegacyLockError("refusing to release a lock without a trusted new owner token")
    move_verified(source, destination, token)
    delete_verified(destination, token)


def main() -> int:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "snapshot":
            value = snapshot(Path(sys.argv[2]))
            print(value.pid, value.lock_format, encode_snapshot(value))
            return 0
        if len(sys.argv) == 3 and sys.argv[1] == "pid-status":
            if re.fullmatch(r"[1-9][0-9]*", sys.argv[2]) is None:
                raise LegacyLockError("PID must be a positive integer")
            print(pid_status(int(sys.argv[2])))
            return 0
        if len(sys.argv) == 5 and sys.argv[1] == "move-verified":
            move_verified(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
            return 0
        if len(sys.argv) == 4 and sys.argv[1] == "delete-verified":
            delete_verified(Path(sys.argv[2]), sys.argv[3])
            return 0
        if len(sys.argv) == 5 and sys.argv[1] == "release-verified":
            release_verified(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
            return 0
        raise LegacyLockError(
            f"usage: {sys.argv[0]} snapshot PATH | pid-status PID | "
            "move-verified SOURCE DESTINATION TOKEN | delete-verified PATH TOKEN | "
            "release-verified SOURCE DESTINATION TOKEN"
        )
    except (LegacyLockError, OSError) as exc:
        print(f"Cannot handle legacy backup lock safely: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
