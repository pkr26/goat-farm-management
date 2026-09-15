#!/usr/bin/env python3
"""Read settings with the application's python-dotenv grammar.

The backup and restore safety gates must interpret ``backend/.env`` exactly as
pydantic-settings does.  In particular, a hand-written ``sed`` parser is not
equivalent for ``export`` declarations, quoting, inline comments, escaping, or
variable interpolation.
"""

from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

_MAX_ENV_FILE_BYTES = 1_048_576


def main() -> int:
    single_key = len(sys.argv) == 3
    complete_snapshot_pairs = (len(sys.argv) - 3) % 2 == 0
    snapshot_mode = len(sys.argv) >= 7 and sys.argv[2] == "--snapshot" and complete_snapshot_pairs
    if not single_key and not snapshot_mode:
        print(
            f"Usage: {sys.argv[0]} ENV_FILE KEY | "
            f"{sys.argv[0]} ENV_FILE --snapshot KEY DEFAULT [KEY DEFAULT ...]",
            file=sys.stderr,
        )
        return 2

    env_file = Path(sys.argv[1])
    key = sys.argv[2] if single_key else None
    # complete_snapshot_pairs already guarantees the two slices are equal in
    # length, so a plain zip() is exact here; zip(strict=True) would only add a
    # Python >= 3.10 requirement to a script that runs under the system python3.
    snapshot_pairs = (
        list(zip(sys.argv[3::2], sys.argv[4::2])) if snapshot_mode else []  # noqa: B905
    )

    try:
        from dotenv import dotenv_values
    except ImportError:
        print(
            "python-dotenv is required to read backend/.env safely; "
            "run this job with the backend virtual environment installed",
            file=sys.stderr,
        )
        return 2

    # Open once and parse the pinned bytes.  A prior ``is_file()`` followed by
    # ``dotenv_values(path)`` was a check/reopen race: removing the file in the
    # gap made python-dotenv return an empty mapping, which this helper reported
    # as "absent" and caused backup/restore to apply development safety
    # defaults.  The descriptor also prevents a pathname replacement from
    # changing which inode is parsed midway through this invocation.
    try:
        # O_NONBLOCK is ignored for ordinary files but prevents a FIFO path
        # from hanging the backup job before fstat can reject it as non-regular.
        fd = os.open(
            env_file,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        if single_key:
            return 3
        values: dict[str, str | None] = {}
    except OSError as exc:
        print(f"Cannot safely read {env_file}: {exc}", file=sys.stderr)
        return 2
    else:
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                print(f"Cannot safely read {env_file}: not a regular file", file=sys.stderr)
                return 2
            if before.st_size > _MAX_ENV_FILE_BYTES:
                print(
                    f"Cannot safely read {env_file}: file exceeds {_MAX_ENV_FILE_BYTES} bytes",
                    file=sys.stderr,
                )
                return 2
            raw = bytearray()
            while len(raw) <= _MAX_ENV_FILE_BYTES:
                chunk = os.read(fd, min(64 * 1024, _MAX_ENV_FILE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
        except OSError as exc:
            print(f"Cannot safely read {env_file}: {exc}", file=sys.stderr)
            return 2
        finally:
            os.close(fd)
        if len(raw) > _MAX_ENV_FILE_BYTES:
            print(
                f"Cannot safely read {env_file}: file exceeds {_MAX_ENV_FILE_BYTES} bytes",
                file=sys.stderr,
            )
            return 2
        if (
            len(raw) != before.st_size
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            print(f"Cannot safely read {env_file}: file changed while it was read", file=sys.stderr)
            return 2
        try:
            content = bytes(raw).decode("utf-8")
            values = dotenv_values(stream=io.StringIO(content), interpolate=True)
        except (OSError, UnicodeError) as exc:
            print(f"Cannot safely parse {env_file}: {exc}", file=sys.stderr)
            return 2

    # BaseSettings is case-insensitive by default. Preserve file order while
    # normalizing so the last spelling of a duplicate key wins the same way.
    normalized_values = {name.lower(): value for name, value in values.items()}
    if snapshot_mode:
        resolved: list[str] = []
        for snapshot_key, default in snapshot_pairs:
            value = normalized_values.get(snapshot_key.lower())
            if value in (None, ""):
                value = default
            # Newline-free records let the Bash caller split the complete
            # snapshot without evaluating or sourcing configuration text. Both
            # settings using this mode are enums, so controls are invalid input
            # in the application too; reject them instead of weakening framing.
            if any(character in value for character in ("\x00", "\r", "\n")):
                print(
                    f"Refusing control character in {snapshot_key} from {env_file}",
                    file=sys.stderr,
                )
                return 2
            resolved.append(value)
        sys.stdout.write("\n".join(resolved))
        return 0

    assert key is not None
    normalized_key = key.lower()
    if normalized_key not in normalized_values or normalized_values[normalized_key] in (None, ""):
        # An empty value ("KEY=") reports absent: the backup/restore ladders
        # historically resolved it through ${VAR:-default}, and hard-failing
        # on an empty placeholder line silently stops cron backups.
        return 3

    value = normalized_values[normalized_key]
    assert value is not None
    if "\x00" in value:
        print(f"Refusing NUL byte in {key} from {env_file}", file=sys.stderr)
        return 2
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
