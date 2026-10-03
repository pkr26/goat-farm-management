"""Reversible fake-fixture checks; never boot production services or send alerts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).resolve().parent
RECEIPTS: list[dict[str, object]] = []


def run(name: str, argv: list[str], *, env: dict[str, str] | None = None, expected: int = 0) -> subprocess.CompletedProcess[str]:
    started = datetime.now(UTC).isoformat()
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    receipt = {"name": name, "command": shlex.join(argv), "cwd": str(ROOT),
               "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
               "expected_exit": expected, "exit_code": result.returncode,
               "stdout": result.stdout, "stderr": result.stderr}
    (REPORT / f"{name}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (REPORT / f"{name}.log").write_text(
        f"Command: {receipt['command']}\nCwd: {ROOT}\nExit code: {result.returncode}\nExpected exit: {expected}\n\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    RECEIPTS.append(receipt)
    assert result.returncode == expected, receipt
    return result


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GOATFARM_")}
    checks: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="herdly-local-ops-proof-") as temporary:
        fixture = Path(temporary)
        directories = {role: fixture / "secrets" / role for role in ("api", "migration", "worker")}
        for role, directory in directories.items():
            directory.mkdir(parents=True)
            (directory / f"{role}-credential.fake").write_text(f"FAKE-{role}-CREDENTIAL\n")
        ca = fixture / "private-ca.pem"
        ca.write_text("FAKE CA CONFIGURATION FIXTURE; NO TLS HANDSHAKE PERFORMED\n")
        jwt = fixture / "jwt"
        jwt.mkdir()
        compose_env = fixture / "deployment.env"
        values = {
            "GOATFARM_BACKEND_IMAGE_REPOSITORY": "example.invalid/herdly/backend",
            "GOATFARM_BACKEND_IMAGE_DIGEST": "sha256:" + "0" * 64,
            "GOATFARM_FRONTEND_IMAGE_REPOSITORY": "example.invalid/herdly/frontend",
            "GOATFARM_FRONTEND_IMAGE_DIGEST": "sha256:" + "1" * 64,
            "GOATFARM_DB_CA_FILE": str(ca), "GOATFARM_JWT_SECRET_DIR": str(jwt),
            "GOATFARM_COMPOSE_ENV_FILE": str(compose_env),
            "GOATFARM_API_SECRET_DIR": str(directories["api"]),
            "GOATFARM_MIGRATION_SECRET_DIR": str(directories["migration"]),
            "GOATFARM_WORKER_SECRET_DIR": str(directories["worker"]),
            "GOATFARM_DATABASE_URL_FILE": "/run/secrets/app/api-credential.fake",
            "GOATFARM_MIGRATION_DATABASE_URL_FILE": "/run/secrets/app/migration-credential.fake",
            "GOATFARM_WORKER_DATABASE_URL_FILE": "/run/secrets/app/worker-credential.fake",
            "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE": "/run/secrets/app/hmac.fake",
            "GOATFARM_TOTP_ENCRYPTION_KEY_FILE": "/run/secrets/app/totp.fake",
            "GOATFARM_METRICS_BEARER_TOKEN_FILE": "/run/secrets/app/metrics.fake",
            "GOATFARM_CORS_ORIGINS": '["https://fixture.example"]',
            "GOATFARM_ALLOWED_HOSTS": '["fixture.example"]',
            "GOATFARM_DOCKER_SUBNET": "172.28.250.0/24", "GOATFARM_EDGE_PROXY_IP": "172.28.250.2",
        }
        compose_env.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        parsed = run("compose-effective-config", ["docker", "compose", "--env-file", str(compose_env),
                     "-f", str(ROOT / "docker-compose.production.yml"), "config", "--format", "json"], env=env)
        configuration = json.loads(parsed.stdout)
        services = configuration["services"]
        expected_services = {"api": "backend", "migration": "migrate", "worker": "screening-worker"}
        app_mounts = {}
        for role, service_name in expected_services.items():
            service = services[service_name]
            secret_mounts = [mount for mount in service["volumes"] if mount["target"] == "/run/secrets/app"]
            assert len(secret_mounts) == 1 and secret_mounts[0]["source"] == str(directories[role])
            assert secret_mounts[0]["read_only"] is True
            assert not service.get("ports"), service_name
            app_mounts[role] = secret_mounts[0]["source"]
        assert len(set(app_mounts.values())) == 3
        assert services["backend"]["environment"]["GOATFARM_DATABASE_URL_FILE"] == values["GOATFARM_DATABASE_URL_FILE"]
        assert services["screening-worker"]["environment"]["GOATFARM_DATABASE_URL_FILE"] == values["GOATFARM_WORKER_DATABASE_URL_FILE"]
        assert "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE" not in services["screening-worker"]["environment"]
        assert "GOATFARM_TOTP_ENCRYPTION_KEY_FILE" not in services["migrate"]["environment"]
        checks["effective_compose"] = {"passed": True, "service_app_mounts": app_mounts,
                                       "api_worker_db_file_routes_distinct": True, "api_not_host_published": True}
        run("compose-file-delivery-guard", [sys.executable, str(ROOT / "backend/scripts/compose_env_guard.py"), str(compose_env)], env=env)

        scripts = fixture / "helper" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(ROOT / "backend/scripts/dotenv_value.py", scripts / "dotenv_value.py")
        (scripts.parent / ".env").write_text(f"GOATFARM_DB_SSLROOTCERT_PATH={ca}\n")
        helper = ["bash", "-c", 'SCRIPT_DIR="$1"; PYTHON_BIN="$2"; source "$3"; load_app_safety_settings; validate_app_settings; printf "%s\\n" "$ENVIRONMENT" "$DB_SSLMODE" "$DB_SSLROOTCERT_PATH"',
                  "fixture", str(scripts), sys.executable, str(ROOT / "backend/scripts/backup_env.sh")]
        classified = {**env, "GOATFARM_ENVIRONMENT": "production", "GOATFARM_DB_SSLMODE": "verify-full"}
        mixed = run("private-ca-mixed-sources", helper, env=classified)
        assert mixed.stdout.splitlines() == ["production", "verify-full", str(ca)]
        exported_ca = fixture / "exported-ca.pem"
        exported_ca.write_text("FAKE EXPORTED CA FIXTURE\n")
        explicit = run("private-ca-export-precedence", helper, env={**classified, "GOATFARM_DB_SSLROOTCERT_PATH": str(exported_ca)})
        assert explicit.stdout.splitlines()[-1] == str(exported_ca)
        checks["private_ca_helper"] = {"passed": True, "mixed_export_and_dotenv": True, "explicit_export_precedence": True,
                                       "scope": "Settings/helper resolution only; no database TLS negotiation"}

        context = fixture / "context"
        context.mkdir()
        shutil.copyfile(ROOT / ".dockerignore", context / ".dockerignore")
        shutil.copytree(fixture / "secrets", context / "secrets")
        (context / ".env").write_text("FAKE_CONTEXT_SECRET=must-not-reach-image\n")
        (context / "visible.fixture").write_text("context probe marker\n")
        (context / "Dockerfile").write_text("FROM scratch\nCOPY . /fixture/\n")
        tag = "herdly-context-proof-20261003:" + uuid.uuid4().hex
        container_name = "herdly-context-proof-" + uuid.uuid4().hex
        image_created = False
        container_created = False
        try:
            run("docker-fixture-context-build", ["docker", "build", "--network=none", "--tag", tag, str(context)], env={**env, "DOCKER_BUILDKIT": "0"})
            image_created = True
            run("docker-fixture-create-no-start", ["docker", "create", "--network=none", "--name", container_name, tag, "/not-executed"])
            container_created = True
            archive = fixture / "fixture-container.tar"
            run("docker-fixture-export", ["docker", "export", "--output", str(archive), container_name])
            with tarfile.open(archive) as exported:
                names = exported.getnames()
            assert "fixture/visible.fixture" in names
            assert not any(name.startswith("fixture/secrets") or name == "fixture/.env" for name in names), names
            checks["docker_fixture_context"] = {"passed": True, "exported_files": names,
                                                "context_secret_sentinels_absent": True, "container_started": False,
                                                "scope": "Actual Docker daemon build/export of scratch fixture with current .dockerignore; production image build remains pending"}
        finally:
            if container_created:
                run("docker-fixture-container-cleanup", ["docker", "rm", container_name])
            if image_created:
                run("docker-fixture-image-cleanup", ["docker", "image", "rm", tag])

    summary = {
        "recorded_at": datetime.now(UTC).isoformat(), "checks": checks,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in [ROOT / ".dockerignore", ROOT / "docker-compose.production.yml",
                                       ROOT / "backend/scripts/backup_env.sh", ROOT / "backend/scripts/compose_env_guard.py"]},
        "receipts": RECEIPTS,
        "pending": ["Production image build and boot", "Real DB private-CA/TLS negotiation", "External provider execution",
                    "Received external test alert", "Production-size migration lock-window rehearsal", "Restart workload soak"],
    }
    (REPORT / "local-probes.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"checks_passed": list(checks), "receipts": len(RECEIPTS), "pending": summary["pending"]}, indent=2))


if __name__ == "__main__":
    main()
