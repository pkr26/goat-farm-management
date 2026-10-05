"""Isolate current Docker deps-stage inputs without modifying repository source."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
OUT = pathlib.Path(__file__).resolve().parent
NODE = "node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81"
records = []
with tempfile.TemporaryDirectory(prefix="goatfarm-a26-frontend-inputs-", dir=pathlib.Path.home() / ".cache") as raw:
    tmp = pathlib.Path(raw)
    for name in ("package.json", "pnpm-lock.yaml"):
        shutil.copyfile(ROOT / "frontend" / name, tmp / name)
    package = json.loads((tmp / "package.json").read_text())
    original_manager = package["packageManager"]
    # Only in this disposable fixture: remove the malformed integrity suffix,
    # so the next independent input failure can be reached.
    package["packageManager"] = "pnpm@9.15.9"
    (tmp / "package.json").write_text(json.dumps(package))
    command = ["docker", "run", "--rm", "-v", f"{tmp}:/app", "-w", "/app", NODE,
               "sh", "-c", "test -f package.json && corepack enable && pnpm --version && pnpm install --frozen-lockfile"]
    missing = subprocess.run(command, capture_output=True, text=True)
    (OUT / "frontend-container-missing-patch.txt").write_text(missing.stdout + missing.stderr)
    records.append({"case": "temporary semver-only packageManager; exact Docker COPY inputs", "exit": missing.returncode,
                    "missing_patch_confirmed": "ENOENT" in missing.stdout + missing.stderr and "braces@3.0.3.patch" in missing.stdout + missing.stderr})
    shutil.copytree(ROOT / "frontend" / "patches", tmp / "patches")
    repaired = subprocess.run(command, capture_output=True, text=True)
    (OUT / "frontend-container-input-positive-control.txt").write_text(repaired.stdout + repaired.stderr)
    records.append({"case": "same temporary packageManager plus repository patches directory", "exit": repaired.returncode})
    print(json.dumps({"node_image": NODE, "original_package_manager": original_manager,
                      "temporary_package_manager": package["packageManager"],
                      "repository_package_sha256_unchanged": hashlib.sha256((ROOT / "frontend/package.json").read_bytes()).hexdigest(),
                      "records": records}, indent=2))
