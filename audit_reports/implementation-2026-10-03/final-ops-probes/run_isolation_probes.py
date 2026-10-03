"""Task-owned fake-data probes; never opens existing operator secret files.

Runs Compose interpolation and disposable filesystem checks, not application
boot. Every container has no network and uses an already cached Postgres image
with its entrypoint replaced by /bin/sh. The builder test uses FROM scratch.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
TOKEN = uuid4().hex[:16]
PREFIX = f"herdly-isolation-probe-{TOKEN}"
LOG = EVIDENCE / "commands-and-results.txt"
clean_env = {key: value for key, value in os.environ.items()
             if not key.startswith(("GOATFARM_", "POSTGRES_", "COMPOSE_"))}
images: list[str] = []
containers: list[str] = []
owned_paths: list[Path] = []
results: dict[str, object] = {"started_utc": datetime.now(timezone.utc).isoformat(), "probe_id": PREFIX}
LOG.write_text("Only invented fixture data is used below. No application service is booted.\n")


def command(args: list[str], *, stdin: str | None = None, expected: int = 0) -> subprocess.CompletedProcess[str]:
    with LOG.open("a") as stream:
        stream.write("\n$ " + shlex.join(args) + "\n")
        if stdin is not None:
            stream.write("# Exact stdin:\n" + stdin + "\n")
    completed = subprocess.run(args, cwd=ROOT, env=clean_env, input=stdin,
                               text=True, capture_output=True, timeout=90)
    with LOG.open("a") as stream:
        stream.write(completed.stdout + completed.stderr + f"\n# exit={completed.returncode}; expected={expected}\n")
    if completed.returncode != expected:
        raise AssertionError(f"unexpected exit {completed.returncode}: {shlex.join(args)}")
    return completed


def dotenv(path: Path, values: dict[str, str]) -> None:
    path.write_text("# Invented isolation-probe values only; not deployment credentials.\n" +
                    "".join(f"{key}={value}\n" for key, value in values.items()))


def fake_file(path: Path, purpose: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    path.write_text(f"FAKE-ISOLATION-PROBE-{TOKEN}-{purpose}\n")
    path.chmod(0o644)


try:
    # mkdir only the random new leaf; existing secrets/ contents are neither
    # enumerated nor read. Remove the parent only if this probe created it.
    secrets_parent = ROOT / "secrets"
    parent_existed = secrets_parent.exists()
    secrets_parent.mkdir(exist_ok=True)
    sentinel = secrets_parent / f"pl07-sentinel-{TOKEN}"
    sentinel.mkdir()
    owned_paths.append(sentinel)
    fake_file(sentinel / "FAKE_SENTINEL_ONLY.txt", "BUILD-CONTEXT")
    # Docker Desktop shares this checkout; platform-private /var/folders
    # temporary paths are not bind-mountable by this daemon.
    temporary = secrets_parent / f"pl02-fixtures-{TOKEN}"
    temporary.mkdir(mode=0o755)
    owned_paths.append(temporary)
    context_source = sentinel.relative_to(ROOT).as_posix()
    ignored = command(["git", "check-ignore", "--", context_source])
    assert context_source in ignored.stdout
    negative_tag = PREFIX + ":excluded"
    images.append(negative_tag)
    rejected = command(["docker", "build", "--no-cache", "--file", "-", "--tag", negative_tag, "."],
                       stdin=f"FROM scratch\nCOPY {context_source}/ /probe/\n", expected=1)
    rejection = rejected.stdout + rejected.stderr
    assert "not found" in rejection or "not found in build context" in rejection
    assert context_source in rejection
    # Positive control has only our fake file as its context; it establishes
    # builder availability without sending any checkout/operator data.
    positive_context = temporary / "positive-context"
    fake_file(positive_context / "positive.txt", "POSITIVE-BUILDER-CONTROL")
    positive_tag = PREFIX + ":positive"
    images.append(positive_tag)
    command(["docker", "build", "--no-cache", "--file", "-", "--tag", positive_tag, str(positive_context)],
            stdin="FROM scratch\nCOPY positive.txt /positive.txt\n")
    results["PL07"] = {"status": "passed", "root_context_excludes_owned_gitignored_secret_directory": context_source,
                       "expected_copy_rejection_exit": 1, "isolated_positive_builder_control_exit": 0}

    directories = {role: temporary / role for role in ("api", "migration", "worker", "jwt")}
    contents = {
        "api": {"database_url", "idempotency_request_hmac_secret", "totp_encryption_key", "metrics_bearer_token", "msg91_auth_key", "s3_access_key_id", "s3_secret_access_key", "screening_anthropic_api_key"},
        "migration": {"migration_database_url"},
        "worker": {"worker_database_url", "s3_access_key_id", "s3_secret_access_key", "screening_anthropic_api_key"},
        "jwt": {"jwt_private.pem", "jwt_public.pem"},
    }
    for role, names in contents.items():
        for name in names:
            fake_file(directories[role] / name, f"{role.upper()}-{name}")
    ca = temporary / "FAKE-postgres-ca.pem"
    fake_file(ca, "CA-FIXTURE-NOT-A-CERTIFICATE")
    production_env = temporary / "production.env"
    production_values = {
        "GOATFARM_COMPOSE_ENV_FILE": str(production_env),
        "GOATFARM_BACKEND_IMAGE_REPOSITORY": "probe.invalid/herdly/backend",
        "GOATFARM_BACKEND_IMAGE_DIGEST": "sha256:" + "1" * 64,
        "GOATFARM_FRONTEND_IMAGE_REPOSITORY": "probe.invalid/herdly/frontend",
        "GOATFARM_FRONTEND_IMAGE_DIGEST": "sha256:" + "2" * 64,
        "GOATFARM_DB_CA_FILE": str(ca), "GOATFARM_JWT_SECRET_DIR": str(directories["jwt"]),
        "GOATFARM_API_SECRET_DIR": str(directories["api"]),
        "GOATFARM_MIGRATION_SECRET_DIR": str(directories["migration"]),
        "GOATFARM_WORKER_SECRET_DIR": str(directories["worker"]),
        "GOATFARM_DATABASE_URL_FILE": "/run/secrets/app/database_url",
        "GOATFARM_MIGRATION_DATABASE_URL_FILE": "/run/secrets/app/migration_database_url",
        "GOATFARM_WORKER_DATABASE_URL_FILE": "/run/secrets/app/worker_database_url",
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE": "/run/secrets/app/idempotency_request_hmac_secret",
        "GOATFARM_TOTP_ENCRYPTION_KEY_FILE": "/run/secrets/app/totp_encryption_key",
        "GOATFARM_METRICS_BEARER_TOKEN_FILE": "/run/secrets/app/metrics_bearer_token",
        "GOATFARM_MSG91_AUTH_KEY_FILE": "/run/secrets/app/msg91_auth_key",
        "GOATFARM_S3_ACCESS_KEY_ID_FILE": "/run/secrets/app/s3_access_key_id",
        "GOATFARM_S3_SECRET_ACCESS_KEY_FILE": "/run/secrets/app/s3_secret_access_key",
        "GOATFARM_SCREENING_ANTHROPIC_API_KEY_FILE": "/run/secrets/app/screening_anthropic_api_key",
        "GOATFARM_CORS_ORIGINS": '["https://probe.invalid"]',
        "GOATFARM_ALLOWED_HOSTS": '["probe.invalid","backend"]',
        "GOATFARM_EDGE_PROXY_IP": "192.168.233.10", "GOATFARM_DOCKER_SUBNET": "192.168.233.0/24",
        "GOATFARM_SCREENING_ENABLED": "false", "GOATFARM_NOTIFICATIONS_ENABLED": "false",
    }
    dotenv(production_env, production_values)
    local_env = temporary / "development.env"
    local_values = {
        "GOATFARM_COMPOSE_ENV_FILE": str(local_env), "POSTGRES_PASSWORD": "FAKE-LOCAL-PROBE-password",
        "GOATFARM_ENVIRONMENT": "development", "GOATFARM_DB_SSLMODE": "disable",
        "GOATFARM_DATABASE_URL": "postgresql+asyncpg://fake_api:FAKE_PASSWORD@db.invalid/probe",
        "GOATFARM_MIGRATION_DATABASE_URL": "postgresql+asyncpg://fake_migration:FAKE_PASSWORD@db.invalid/probe",
        "GOATFARM_WORKER_DATABASE_URL": "postgresql+asyncpg://fake_worker:FAKE_PASSWORD@db.invalid/probe",
        "GOATFARM_API_SECRET_DIR": str(directories["api"]),
        "GOATFARM_MIGRATION_SECRET_DIR": str(directories["migration"]),
        "GOATFARM_WORKER_SECRET_DIR": str(directories["worker"]),
        "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET": "FAKE-PROBE-ONLY-NOT-A-DEPLOYMENT-SECRET-" + TOKEN,
        "GOATFARM_SCREENING_ENABLED": "false", "GOATFARM_NOTIFICATIONS_ENABLED": "false",
    }
    dotenv(local_env, local_values)
    configs: dict[str, dict[str, object]] = {}
    for mode, file, env_file in (("development", "docker-compose.yml", local_env), ("production", "docker-compose.production.yml", production_env)):
        rendered = command(["docker", "compose", "--project-name", PREFIX, "--env-file", str(env_file), "-f", file, "config", "--format", "json"])
        config = json.loads(rendered.stdout)
        configs[mode] = config
        (EVIDENCE / f"{mode}-resolved-fake-config.json").write_text(json.dumps(config, indent=2) + "\n")
    services = configs["production"]["services"]
    mount_sources = {}
    for service, role in (("backend", "api"), ("migrate", "migration"), ("screening-worker", "worker")):
        selected = [mount for mount in services[service]["volumes"] if mount["target"] == "/run/secrets/app"]
        assert len(selected) == 1 and selected[0]["read_only"] is True
        assert Path(selected[0]["source"]) == directories[role]
        mount_sources[service] = selected[0]["source"]
    assert len(set(mount_sources.values())) == 3
    assert services["backend"]["environment"]["GOATFARM_DATABASE_URL_FILE"] == "/run/secrets/app/database_url"
    assert services["migrate"]["environment"]["GOATFARM_MIGRATION_DATABASE_URL_FILE"] == "/run/secrets/app/migration_database_url"
    assert services["screening-worker"]["environment"]["GOATFARM_DATABASE_URL_FILE"] == "/run/secrets/app/worker_database_url"
    assert "GOATFARM_MIGRATION_DATABASE_URL_FILE" not in services["backend"]["environment"]
    assert "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE" not in services["migrate"]["environment"]
    assert "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET_FILE" not in services["screening-worker"]["environment"]
    guard = [str(ROOT / "backend/.venv/bin/python"), str(ROOT / "backend/scripts/compose_env_guard.py")]
    command([*guard, str(local_env)])
    command([*guard, str(production_env)])
    for required in ("GOATFARM_DATABASE_URL", "GOATFARM_MIGRATION_DATABASE_URL", "GOATFARM_WORKER_DATABASE_URL", "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET"):
        missing = production_values.copy()
        del missing[required + "_FILE"]
        missing_file = temporary / f"missing-{required}.env"
        dotenv(missing_file, missing)
        failure = command([*guard, str(missing_file)], expected=2)
        assert f"neither {required} nor {required}_FILE is set" in failure.stderr
    overlapping = production_values | {"GOATFARM_WORKER_SECRET_DIR": str(directories["api"])}
    overlapping_file = temporary / "overlapping-directories.env"
    dotenv(overlapping_file, overlapping)
    assert "must be distinct" in command([*guard, str(overlapping_file)], expected=2).stderr

    cached_image = command(["docker", "image", "inspect", "postgres:16", "--format", "{{.Id}}"] ).stdout.strip()
    all_names = set().union(*contents.values())
    mount_receipts = {}
    for service, role in (("backend", "api"), ("migrate", "migration"), ("screening-worker", "worker")):
        name = f"{PREFIX}-{role}"
        containers.append(name)
        cmd = ["docker", "run", "--pull=never", "--rm", "--name", name, "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=10001:10001", "--entrypoint=/bin/sh"]
        mounted = [mount for mount in services[service]["volumes"] if mount["target"].startswith("/run/secrets/")]
        for mount in mounted:
            assert mount["type"] == "bind" and mount["read_only"] is True
            cmd += ["--mount", f'type=bind,src={mount["source"]},dst={mount["target"]},readonly']
        allowed = contents[role]
        checks = [f"test -r /run/secrets/app/{shlex.quote(filename)}" for filename in sorted(allowed)]
        forbidden = all_names - allowed
        checks += [f"test ! -e /run/secrets/app/{shlex.quote(filename)}" for filename in sorted(forbidden)]
        checks.append("test -r /run/secrets/goatfarm-postgres-ca.pem")
        if role == "api":
            checks += ["test -r /run/secrets/jwt/jwt_private.pem", "test -r /run/secrets/jwt/jwt_public.pem"]
        else:
            checks += ["test ! -e /run/secrets/jwt", "test ! -e /run/secrets/jwt/jwt_private.pem"]
        # Prints names and mode only, never contents (even invented values).
        checks.append("find /run/secrets -type f -print | sort")
        checks.append("printf 'UID=%s; allowed fake files readable; forbidden files absent\\n' \"$(id -u)\"")
        receipt = command([*cmd, cached_image, "-ec", "\n".join(checks)])
        mount_receipts[service] = {"role": role, "allowed_fake_filenames": sorted(allowed), "forbidden_fake_filenames": sorted(forbidden), "output": receipt.stdout}
    results["PL02"] = {"status": "passed", "resolved_distinct_app_mount_sources": mount_sources,
                       "required_delivery_routes": "API DB, migration DB, worker DB and idempotency HMAC; each missing route rejected by actual guard",
                       "overlapping_directory_guard_exit": 2, "cached_probe_image_id": cached_image,
                       "mount_receipts": mount_receipts,
                       "scope": "Compose resolution, delivery presence and filesystem isolation only; no production/API/migration/worker process boot, real secret reads, database access or provider/delivery calls"}
except Exception as error:
    results["failure"] = f"{type(error).__name__}: {error}"
    raise
finally:
    cleanup = []
    for name in containers:
        result = subprocess.run(["docker", "container", "inspect", name], cwd=ROOT, env=clean_env, capture_output=True, text=True)
        if result.returncode == 0:
            command(["docker", "rm", "--force", name])
        cleanup.append({"container": name, "absent": subprocess.run(["docker", "container", "inspect", name], env=clean_env, capture_output=True).returncode != 0})
    for tag in images:
        result = subprocess.run(["docker", "image", "inspect", tag], cwd=ROOT, env=clean_env, capture_output=True, text=True)
        if result.returncode == 0:
            command(["docker", "image", "rm", tag])
        cleanup.append({"image": tag, "absent": subprocess.run(["docker", "image", "inspect", tag], env=clean_env, capture_output=True).returncode != 0})
    for path in reversed(owned_paths):
        shutil.rmtree(path)
        cleanup.append({"owned_path": str(path), "absent": not path.exists()})
    if "parent_existed" in globals() and not parent_existed:
        try:
            secrets_parent.rmdir()
        except OSError:
            pass  # Another task may have created its own leaf meanwhile.
    results["cleanup"] = cleanup
    results["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (EVIDENCE / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"PL07": results.get("PL07", {}).get("status"), "PL02": results.get("PL02", {}).get("status"), "failure": results.get("failure"), "all_owned_artifacts_removed": all(item["absent"] for item in cleanup)}, indent=2))
