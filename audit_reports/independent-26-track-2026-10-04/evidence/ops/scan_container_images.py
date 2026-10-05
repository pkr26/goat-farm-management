"""Scan exact local pinned images with a freshly downloaded isolated Trivy DB."""
import collections
import hashlib
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[4]
OUT = pathlib.Path(__file__).resolve().parent
SCRATCH = pathlib.Path.home() / ".cache/goatfarm-a26-container"
TRIVY = "aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
IMAGES = {
    "python-base": "python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285",
    "node-base": "node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81",
    "nginx-base": "nginx:1.30.5-alpine3.24@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94",
    "postgres-service": "postgres:16@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94",
    "edge-current-build": "goatfarm-a26-edge:7245368",
    "backend-current-build": "goatfarm-a26-backend:7245368",
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
            if label == "postgres-service":
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
    manifest["images"].append(record)
    (OUT / "container-scan-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"label": label, "raw": record["raw"].get("severity_counts"), "repository_policy": record["repository-policy"].get("severity_counts"), "gate_exit": record["repository-policy"]["exit"]}), flush=True)
