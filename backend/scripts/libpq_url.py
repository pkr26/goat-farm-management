#!/usr/bin/env python3
"""Turn an application PostgreSQL URL into credential-safe libpq inputs.

The URL is deliberately read from stdin so a database password never appears
in this helper's process arguments.  A private passfile is written at the path
supplied by the caller; stdout contains the password-free URL and decoded
database name on separate lines.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

# Allowlist, not a denylist: libpq keeps adding TLS-trust and connection
# identity parameters (sslrootcert, sslcert, sslkey, sslcrl, sslcrldir,
# gssencmode, channel_binding, sslnegotiation, krbsrvname, requirepeer,
# passfile, service, ...), and a URL query parameter overrides the
# operator-exported PGSSLMODE/PGPASSFILE environment.  A URL that smuggled
# e.g. sslrootcert could make the enforced verify-full gate verify against
# an attacker-chosen CA.  Only parameters that cannot alter where libpq
# connects or how trustingly it authenticates the server may ride in the
# query; everything else is rejected loudly.
ALLOWED_QUERY_KEYS = frozenset({"application_name", "connect_timeout"})


def fail(message: str) -> NoReturn:
    print(f"Invalid PostgreSQL URL: {message}", file=sys.stderr)
    raise SystemExit(2)


def reject_controls(label: str, value: str) -> None:
    if re.search(r"[\x00-\x1f\x7f]", value):
        fail(f"{label} contains a control character")


def passfile_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def create_private_file(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(content.encode())
        while remaining:
            written = os.write(fd, remaining)
            if written == 0:
                raise OSError("passfile write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} PASSFILE", file=sys.stderr)
        raise SystemExit(2)

    raw_url = sys.stdin.read()
    reject_controls("URL", raw_url)
    parts = urlsplit(raw_url)
    if parts.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        fail("scheme must be postgres, postgresql, or postgresql+asyncpg")
    if parts.fragment:
        fail("fragments are not supported")
    if parts.hostname is None:
        fail("host is required")
    try:
        port = parts.port
    except ValueError as exc:
        fail(str(exc))

    username = unquote(parts.username) if parts.username is not None else ""
    password = unquote(parts.password) if parts.password is not None else None
    host = parts.hostname
    database = unquote(parts.path.removeprefix("/"))
    if not database or "/" in database:
        fail("exactly one database name is required")
    for label, value in (
        ("username", username),
        ("password", password or ""),
        ("host", host),
        ("database name", database),
    ):
        reject_controls(label, value)

    for key, _value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() not in ALLOWED_QUERY_KEYS:
            fail(
                f"query parameter {key!r} is not permitted; only "
                f"{', '.join(sorted(ALLOWED_QUERY_KEYS))} may appear in the URL query"
            )

    rendered_host = f"[{host}]" if ":" in host else host
    rendered_user = f"{quote(username, safe='')}@" if username else ""
    rendered_port = f":{port}" if port is not None else ""
    safe_netloc = f"{rendered_user}{rendered_host}{rendered_port}"
    safe_url = urlunsplit(
        ("postgresql", safe_netloc, f"/{quote(database, safe='')}", parts.query, "")
    )

    passfile_content = ""
    if password is not None:
        passfile_content = (
            ":".join(
                passfile_escape(value)
                for value in (
                    host,
                    str(port) if port is not None else "*",
                    database,
                    username or "*",
                    password,
                )
            )
            + "\n"
        )
    create_private_file(Path(sys.argv[1]), passfile_content)
    print(safe_url)
    print(database)


if __name__ == "__main__":
    main()
