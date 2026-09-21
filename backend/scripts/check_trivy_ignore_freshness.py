#!/usr/bin/env python3
"""Fail CI when the compose-image Trivy ignore file goes stale (L-5,
2026-09-20 audit).

`.trivyignore.compose-images` masks acknowledged CVEs in the digest-pinned
third-party images (nginx, PostgreSQL) until upstream publishes rebuilt tags.
The documented refresh policy is weekly, but nothing enforced it: an operator
who never re-checks the pinned digests ships suppressed CVEs indefinitely.

The file carries a `# refreshed: YYYY-MM-DD` marker that MUST be updated in
the same change that refreshes the digests (and prunes entries whose CVEs no
longer appear). This script fails when that marker is missing, malformed, or
older than --max-age days (default 30, one refresh cycle of slack over the
weekly policy; override with GOATFARM_TRIVY_IGNORE_MAX_AGE_DAYS for tests).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IGNORE_FILE = REPO_ROOT / ".trivyignore.compose-images"
MARKER_RE = re.compile(r"^#\s*refreshed:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
DEFAULT_MAX_AGE_DAYS = 30


def parse_refreshed_marker(text: str) -> date | None:
    match = MARKER_RE.search(text)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ignore-file",
        type=Path,
        default=DEFAULT_IGNORE_FILE,
        help="path to the compose-image trivy ignore file",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=int(
            __import__("os").environ.get("GOATFARM_TRIVY_IGNORE_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)
        ),
        help="maximum age of the refreshed marker before CI fails",
    )
    parser.add_argument(
        "--today",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=None,
        help="override the current date (tests only)",
    )
    args = parser.parse_args()

    if args.max_age_days < 1:
        print(f"error: --max-age-days must be positive (got {args.max_age_days})", file=sys.stderr)
        return 2

    text = args.ignore_file.read_text(encoding="utf-8")
    refreshed = parse_refreshed_marker(text)
    if refreshed is None:
        print(
            f"error: {args.ignore_file.name} has no '# refreshed: YYYY-MM-DD' marker. "
            "Add it in the same change that refreshes the pinned image digests.",
            file=sys.stderr,
        )
        return 1

    today = args.today or date.today()
    if refreshed > today:
        print(
            f"error: {args.ignore_file.name} refreshed marker {refreshed} is in the future.",
            file=sys.stderr,
        )
        return 1
    age = today - refreshed
    if age > timedelta(days=args.max_age_days):
        print(
            f"error: {args.ignore_file.name} was last refreshed {age.days} days ago "
            f"(marker {refreshed}); the policy allows {args.max_age_days}. "
            "Refresh the pinned nginx/PostgreSQL digests (docker pull <tag>, "
            "docker manifest inspect for the digest, re-scan with trivy, prune "
            "fixed entries) and update the marker in the same change.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: ignore file refreshed {age.days} day(s) ago (marker {refreshed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
