#!/usr/bin/env python3
"""Prove release references are absent; inspection failures never permit publication."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PreflightError(ValueError):
    """The release already exists, or its absence could not be established."""


def _api(endpoint: str, *, paginate: bool = False) -> Any:
    command = ["gh", "api"]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    command.append(endpoint)
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError("GitHub inspection did not complete; refusing publication") from exc
    if result.returncode:
        # Do not echo API error output: credentials may be present in transport errors.
        raise PreflightError("GitHub inspection failed; refusing publication")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise PreflightError("GitHub inspection returned invalid JSON") from exc


def _pages(endpoint: str) -> list[dict[str, Any]]:
    pages = _api(endpoint, paginate=True)
    if not isinstance(pages, list) or not pages:
        raise PreflightError("GitHub inspection returned no completed pages")
    records: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise PreflightError("GitHub inspection returned malformed pages")
        records.extend(page)
    return records


def _manifest_absent(image: str, tag: str, *, actor: str, credential: str) -> None:
    repository = image.removeprefix("ghcr.io/")
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    basic = base64.b64encode(f"{actor}:{credential}".encode()).decode("ascii")
    token_request = urllib.request.Request(
        f"https://ghcr.io/token?{query}", headers={"Authorization": f"Basic {basic}"}
    )
    try:
        with urllib.request.urlopen(token_request, timeout=30) as response:
            token_data = json.loads(response.read(65537))
        token = token_data.get("token") if isinstance(token_data, dict) else None
        if not isinstance(token, str) or not token or len(token) > 65536:
            raise PreflightError("GHCR inspection returned no valid pull token")
        manifest_request = urllib.request.Request(
            f"https://ghcr.io/v2/{repository}/manifests/{urllib.parse.quote(tag, safe='')}",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "application/vnd.oci.image.index.v1+json, "
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.list.v2+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        try:
            with urllib.request.urlopen(manifest_request, timeout=30) as response:
                if response.status == 200:
                    raise PreflightError(f"Immutable image tag {image}:{tag} already exists")
                raise PreflightError("GHCR inspection returned an unexpected status")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # A successful authenticated pull-token request and explicit registry
                # 404 prove this tag absent. All other HTTP/transport errors fail closed.
                return
            raise PreflightError("GHCR manifest inspection failed; refusing publication") from exc
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, PreflightError):
            raise
        raise PreflightError("GHCR inspection did not complete; refusing publication") from exc


def main() -> int:
    try:
        owner = os.environ["RELEASE_OWNER"].lower()
        repository = os.environ["GITHUB_REPOSITORY"]
        tag = os.environ["TAG"]
        images = [os.environ[name] for name in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "EDGE_IMAGE")]
        if not tag or any(
            not image.startswith(f"ghcr.io/{owner}/")
            or "/" in image.removeprefix(f"ghcr.io/{owner}/")
            or not image.removeprefix(f"ghcr.io/{owner}/")
            for image in images
        ):
            raise PreflightError("Unexpected release image namespace or empty tag")
        releases = _pages(f"repos/{repository}/releases?per_page=100")
        if any(not isinstance(release.get("tag_name"), str) for release in releases):
            raise PreflightError("GitHub release inspection returned malformed records")
        if any(release["tag_name"] == tag for release in releases):
            raise PreflightError(f"Release {tag} already exists; use a new version")
        account = _api(f"users/{owner}")
        account_type = account.get("type") if isinstance(account, dict) else None
        if not isinstance(account_type, str) or account_type not in {
            "User",
            "Organization",
        }:
            raise PreflightError("Could not establish the release owner's account type")
        namespace = "orgs" if account_type == "Organization" else "users"
        packages = _pages(f"{namespace}/{owner}/packages?package_type=container&per_page=100")
        if any(not isinstance(package.get("name"), str) for package in packages):
            raise PreflightError("GitHub package inspection returned malformed records")
        package_names = {package["name"] for package in packages}
        for image in images:
            # A successful complete package list proves a first release's missing
            # repository absent, without requesting a pull token for a nonexistent package.
            if image.rsplit("/", 1)[1] in package_names:
                _manifest_absent(
                    image,
                    tag,
                    actor=os.environ["GITHUB_ACTOR"],
                    credential=os.environ["GH_TOKEN"],
                )
        print(f"Verified release {tag} and all immutable image tags are absent")
        return 0
    except (KeyError, PreflightError) as exc:
        print(f"::error::Release preflight rejected: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
