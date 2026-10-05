#!/usr/bin/env python3
"""Remove only exact package versions published by this failed release attempt.

Missing receipts (including rejected preflight) cause no registry calls. A
version with any unowned tag is retained: GHCR deletes versions, not tags.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def main() -> int:
    owner = os.environ["RELEASE_OWNER"].lower()
    records = json.loads(os.environ.get("OWNED_PUBLISHES", "[]"))
    run_identity = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    receipt = Path(os.environ["RUNNER_TEMP"]) / f"goatfarm-release-{run_identity}-owned.jsonl"
    if receipt.is_file():
        records.extend(json.loads(line) for line in receipt.read_text().splitlines() if line)
    owned: dict[tuple[str, str], set[str]] = {}
    for record in records:
        digest = record.get("digest", "")
        image = record.get("image", "")
        tag = record.get("tag", "")
        if DIGEST.fullmatch(digest) and image.startswith(f"ghcr.io/{owner}/") and tag:
            owned.setdefault((image, digest), set()).add(tag)
    if not owned:
        print("No successfully published package-version receipts; cleanup is a no-op.")
        return 0
    organization = (
        subprocess.run(["gh", "api", f"orgs/{owner}"], capture_output=True, check=False).returncode
        == 0
    )
    api_base = f"orgs/{owner}" if organization else "user"
    for (image, digest), tags in sorted(owned.items()):
        package = image.rsplit("/", 1)[1]
        endpoint = f"{api_base}/packages/container/{package}/versions"
        listed = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", endpoint],
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode:
            print(f"::warning::Cannot inspect owned {image}@{digest}; retained for manual cleanup.")
            continue
        pages: list[list[dict[str, Any]]] = json.loads(listed.stdout)
        for version in (version for page in pages for version in page):
            if version.get("name") != digest:
                continue
            actual_tags = set(version.get("metadata", {}).get("container", {}).get("tags", []))
            if not actual_tags or not actual_tags.issubset(tags):
                print(f"::warning::Retaining {image}@{digest}: tags are not exclusively owned.")
                continue
            identifier = version.get("id")
            if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                raise ValueError("Invalid package version ID")
            deleted = subprocess.run(
                ["gh", "api", "-X", "DELETE", f"{endpoint}/{identifier}"], check=False
            )
            if deleted.returncode:
                print(f"::warning::Could not remove owned {image}@{digest}; remove it manually.")
            else:
                print(f"Removed owned {image}@{digest} (version {identifier}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
