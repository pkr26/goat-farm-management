#!/usr/bin/env python3
"""Fetch reviewed, exact Debian package bytes for the target architecture.

The signed Debian package indexes establish these initial URL/hash pins; builds
use the committed hashes directly, without resolving mutable package indexes.
"""

import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("manifest", type=Path)
parser.add_argument("destination", type=Path)
parser.add_argument("--package", action="append", default=[])
args = parser.parse_args()
architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
entries = json.loads(args.manifest.read_text())[architecture]
selected = [entry for entry in entries if not args.package or entry["package"] in args.package]
if not selected or (args.package and {entry["package"] for entry in selected} != set(args.package)):
    raise SystemExit("Unknown or missing pinned Debian security package")
args.destination.mkdir(parents=True, exist_ok=True)
for entry in selected:
    with urllib.request.urlopen(entry["url"], timeout=60) as response:
        data = response.read(entry["bytes"] + 1)
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise SystemExit(f"Pinned package integrity failed: {entry['package']}")
    target = args.destination / entry["filename"]
    target.write_bytes(data)
    # Validate package metadata too: pins must not silently target another ABI or package.
    actual = subprocess.check_output(
        ["dpkg-deb", "-f", str(target), "Package", "Version", "Architecture"], text=True
    ).splitlines()
    expected = [
        f"Package: {entry['package']}",
        f"Version: {entry['version']}",
        f"Architecture: {architecture}",
    ]
    if actual != expected:
        raise SystemExit(f"Pinned package metadata mismatch: {entry['package']}: {actual}")
