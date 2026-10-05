"""Scan exact local pinned images with a freshly downloaded isolated Trivy DB."""
import collections
import hashlib
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
SCRATCH = pathlib.Path.home() / ".cache/goatfarm-fix29-ops"
TRIVY = "aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
IMAGES = {
    "postgres-service": "goatfarm-fix29-postgres:104",
    "backend-current-build": "goatfarm-fix29-backend:104",
    "frontend-current-build": "goatfarm-fix29-frontend:104",
    "postgres-service-amd64": "goatfarm-fix29-postgres:104-amd64",
    "backend-current-build-amd64": "goatfarm-fix29-backend:104-amd64",
}

metadata = json.loads((SCRATCH / "cache/db/metadata.json").read_text())
ignore = (ROOT / ".trivyignore.compose-images").read_bytes()
(OUT / "trivy-postgres-ignore-policy.txt").write_bytes(ignore)
manifest = {"scanner": TRIVY, "db_metadata": metadata,
            "db_sha256": hashlib.sha256((SCRATCH / "cache/db/trivy.db").read_bytes()).hexdigest(),
            "scan_started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "postgres_ignore_sha256": hashlib.sha256(ignore).hexdigest(),
            "raw_policy": "all severities, all fixed/unfixed, scanners=vuln, no ignore file, offline analysis, fresh DB",
            "repo_gate_policy": "HIGH,CRITICAL; ignore-unfixed; postgres-only explicit ignore file; scanners=vuln (secrets not claimed)",
            "images": []}
for label, ref in IMAGES.items():
    image_info = json.loads(subprocess.check_output(["docker", "image", "inspect", ref], text=True))[0]
    identifier = image_info["Id"]
    record = {"label": label, "requested_ref": ref, "immutable_local_image_id": identifier,
              "repo_digests": image_info.get("RepoDigests", []), "architecture": image_info["Architecture"], "os": image_info["Os"]}
    archive = SCRATCH / "images" / (label + ".tar")
    subprocess.run(["docker", "image", "save", "--output", str(archive), identifier], check=True)
    common = ["docker", "run", "--rm", "--network", "none", "-v", f"{SCRATCH / 'cache'}:/root/.cache/trivy",
              "-v", f"{SCRATCH / 'images'}:/inputs:ro", "-v", f"{OUT}:/reports", TRIVY,
              "image", "--input", f"/inputs/{archive.name}", "--skip-db-update", "--skip-java-db-update", "--offline-scan",
              "--scanners", "vuln", "--format", "json", "--timeout", "5m", "--quiet"]
    for mode in ("raw", "repository-policy"):
        output = OUT / f"trivy-{label}-{mode}.json"
        command = common + ["--output", f"/reports/{output.name}"]
        if mode == "repository-policy":
            command += ["--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1"]
            if label.startswith("postgres-service"):
                command += ["--ignorefile", "/reports/trivy-postgres-ignore-policy.txt"]
        completed = subprocess.run(command, capture_output=True, text=True)
        (OUT / f"trivy-{label}-{mode}.stderr").write_text(completed.stdout + completed.stderr)
        result = {"exit": completed.returncode, "command": command}
        if output.exists():
            report = json.loads(output.read_text())
            vulnerabilities = [v for target in report.get("Results", []) for v in target.get("Vulnerabilities", [])]
            result.update({"vulnerability_records": len(vulnerabilities), "severity_counts": dict(collections.Counter(v["Severity"] for v in vulnerabilities)),
                           "unique_ids": sorted(set(v["VulnerabilityID"] for v in vulnerabilities))})
        record[mode] = result
    sbom = OUT / f"{label}.spdx.json"
    sbom_command = [value if value != "json" else "spdx-json" for value in common]
    sbom_run = subprocess.run(sbom_command + ["--output", f"/reports/{sbom.name}"], capture_output=True, text=True)
    record["sbom"] = {"exit": sbom_run.returncode, "file": sbom.name, "sha256": hashlib.sha256(sbom.read_bytes()).hexdigest() if sbom.exists() else None}
    (OUT / f"{label}-sbom.stderr").write_text(sbom_run.stdout + sbom_run.stderr)
    manifest["images"].append(record)
    (OUT / "container-scan-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"label": label, "raw": record["raw"].get("severity_counts"), "repository_policy": record["repository-policy"].get("severity_counts"), "gate_exit": record["repository-policy"]["exit"]}), flush=True)
