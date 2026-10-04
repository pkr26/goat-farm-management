"""Direct acceptance receipts for PIN capability and the actual CI Git gate.

The Git tests execute the current YAML step, not a parallel reimplementation.
All repositories are synthetic tmp_path directories; API tests use the suite's
explicitly named disposable PostgreSQL database and no external providers.
"""

import os
import subprocess
from pathlib import Path
from typing import Literal

import httpx
import pytest
import yaml

from app.db import get_sessionmaker
from app.models import FarmMembership

from .conftest import OWNER_PW, login, owner_with_farm, provisioned_worker_login
from .type_helpers import Headers, JsonObject, json_int, json_object, json_objects, json_string

REPO = Path(__file__).resolve().parents[2]
FORMERLY_EXEMPT = (
    "bd201c1cdc1b_goat_only_simplification.py",
    "b5d7f9a1c3e5_backfill_species_reference_data.py",
    "c8f1d3a5e709_finance_feed_task_integrity.py",
)


async def _memberships(client: httpx.AsyncClient, headers: Headers) -> list[JsonObject]:
    response = await client.get("/api/team", headers=headers)
    assert response.status_code == 200, response.text
    return json_objects(json_object(response.json())["memberships"])


async def _pin_worker(client: httpx.AsyncClient, owner: Headers, email: str) -> JsonObject:
    role = await client.post(
        "/api/team/roles",
        json={"name": "Evidence tablet worker", "permissions": ["tasks.view", "tasks.complete"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    created = await client.post(
        "/api/team/workers",
        json={
            "name": "Synthetic evidence worker",
            "email": email,
            "role_id": json_int(json_object(role.json())["id"]),
            "pin": "4321",
        },
        headers={**owner, "Idempotency-Key": f"create-evidence-{email}"},
    )
    assert created.status_code == 201, created.text
    return json_object(created.json())


@pytest.mark.parametrize("target", ["eligible-worker", "owning-identity"])
async def test_pin_reset_response_matches_live_roster_credential_eligibility(
    client: httpx.AsyncClient, target: str
) -> None:
    email = f"pin-evidence-{target}@farm.in"
    owner = await owner_with_farm(client, email=email)
    if target == "eligible-worker":
        selected = await _pin_worker(client, owner, "eligible-evidence-worker@farm.in")
        expected_capability = True
    else:
        # Imported/global owners may also have an operational membership.
        # Seed that valid relationship; its credentials remain self-service.
        me = await client.get("/api/auth/me", headers=owner)
        assert me.status_code == 200, me.text
        operational_role = await client.post(
            "/api/team/roles",
            json={"name": "Synthetic owner operations", "permissions": ["tasks.view"]},
            headers=owner,
        )
        assert operational_role.status_code == 201, operational_role.text
        async with get_sessionmaker()() as db:
            db.add(
                FarmMembership(
                    farm_id=int(owner["X-Farm-Id"]),
                    user_id=json_int(json_object(me.json())["id"]),
                    role_id=json_int(json_object(operational_role.json())["id"]),
                    is_active=True,
                    account_provisioned_by_farm=False,
                )
            )
            await db.commit()
        selected = next(
            item for item in await _memberships(client, owner) if item["email"] == email
        )
        expected_capability = False
    membership_id = json_int(selected["id"])
    before = next(item for item in await _memberships(client, owner) if item["id"] == membership_id)
    assert before["can_reset_password"] is expected_capability
    response = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "998877"},
        headers={**owner, "Idempotency-Key": f"pin-evidence-{target}"},
    )
    assert response.status_code == 200, response.text
    immediate = json_object(response.json())
    assert immediate["id"] == membership_id
    assert immediate["pin_set"] is True
    assert immediate["can_reset_password"] is expected_capability
    assert immediate["reset_password_block_reason"] == before["reset_password_block_reason"]
    if target == "owning-identity":
        # Setting this owner's PIN revokes its captured access generation.
        # Fetch with a fresh password login instead of weakening that fence.
        revoked = await client.get("/api/auth/me", headers=owner)
        assert revoked.status_code == 401, revoked.text
        fresh = await login(client, email, OWNER_PW)
        owner = {**fresh, "X-Farm-Id": owner["X-Farm-Id"]}
    after = next(item for item in await _memberships(client, owner) if item["id"] == membership_id)
    for field in ("can_reset_password", "reset_password_block_reason", "pin_set"):
        assert immediate[field] == after[field], field
    if expected_capability:
        assert immediate["reset_password_block_reason"] is None
    else:
        assert isinstance(immediate["reset_password_block_reason"], str)
        assert immediate["reset_password_block_reason"]


async def test_delegated_team_manager_cannot_reset_pin_or_change_owner_capability(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="evidence-owner-denial@farm.in")
    worker = await _pin_worker(client, owner, "evidence-protected-worker@farm.in")
    membership_id = json_int(worker["id"])
    manager_role = await client.post(
        "/api/team/roles",
        json={"name": "Evidence delegated manager", "permissions": ["team.manage"]},
        headers=owner,
    )
    assert manager_role.status_code == 201, manager_role.text
    manager_email = "evidence-delegated-manager@farm.in"
    manager_password = "synthetic-manager-pass-123"
    created = await client.post(
        "/api/team/workers",
        json={
            "email": manager_email,
            "password": manager_password,
            "role_id": json_int(json_object(manager_role.json())["id"]),
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    manager, _ = await provisioned_worker_login(client, manager_email, manager_password)
    manager["X-Farm-Id"] = owner["X-Farm-Id"]
    manager_view = next(
        item for item in await _memberships(client, manager) if item["id"] == membership_id
    )
    assert manager_view["can_reset_password"] is False
    denied = await client.post(
        f"/api/team/workers/{membership_id}/reset-pin",
        json={"pin": "998877"},
        headers=manager,
    )
    assert denied.status_code == 403, denied.text
    assert json_object(denied.json())["detail"] == manager_view["reset_password_block_reason"]
    old_pin = await client.post(
        "/api/auth/worker-login",
        json={"farm_id": int(owner["X-Farm-Id"]), "membership_id": membership_id, "pin": "4321"},
    )
    assert old_pin.status_code == 200, old_pin.text
    owner_view = next(
        item for item in await _memberships(client, owner) if item["id"] == membership_id
    )
    assert owner_view["can_reset_password"] is True
    assert owner_view["reset_password_block_reason"] is None


def _git_environment() -> dict[str, str]:
    # Never inherit an outer checkout/index/worktree or operator Git config.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _synthetic_repository(tmp_path: Path, filename: str) -> tuple[Path, Path, str]:
    repository = tmp_path / "synthetic-gate-repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Synthetic Evidence")
    _git(repository, "config", "user.email", "synthetic-evidence@example.invalid")
    _git(repository, "config", "commit.gpgsign", "false")
    _git(repository, "config", "core.hooksPath", os.devnull)
    migration = repository / "backend" / "alembic" / "versions" / filename
    migration.parent.mkdir(parents=True)
    migration.write_text("revision = 'synthetic_old'\ndown_revision = None\n", encoding="utf-8")
    (repository / "README.md").write_text(
        "Synthetic immutable history fixture.\n", encoding="utf-8"
    )
    base = _commit(repository, "Create synthetic applied revision")
    return repository, migration, base


def _actual_guard() -> str:
    # BaseLoader preserves GitHub's YAML 1.2 `on` key rather than converting
    # it into the YAML 1.1 boolean True; the literal run string is unchanged.
    workflow = json_object(
        yaml.load((REPO / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    )
    jobs = json_object(workflow["jobs"])
    job = json_object(jobs["migration-immutability"])
    steps = json_objects(job["steps"])
    step = next(
        item for item in steps if item.get("name") == "Applied Alembic revisions are immutable"
    )
    return json_string(step["run"])


def _execute_guard(
    repository: Path,
    base: str,
    event: Literal["pull_request", "push"],
    *,
    before: str | None = None,
) -> subprocess.CompletedProcess[str]:
    guard = _actual_guard()
    substitutions = {
        "${{ github.event_name }}": event,
        "${{ github.event.pull_request.base.sha }}": base,
        "${{ github.event.before }}": before if before is not None else base,
    }
    for expression, value in substitutions.items():
        guard = guard.replace(expression, value)
    assert "${{" not in guard, "Unexpected unevaluated GitHub expression in exact CI step"
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", guard],
        cwd=repository,
        env=_git_environment(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("filename", FORMERLY_EXEMPT)
@pytest.mark.parametrize("event", ["pull_request", "push"])
@pytest.mark.parametrize("operation", ["modify", "delete", "rename", "forward-add"])
def test_actual_ci_guard_rejects_applied_changes_and_accepts_forward_additions(
    tmp_path: Path,
    filename: str,
    event: Literal["pull_request", "push"],
    operation: str,
) -> None:
    repository, migration, base = _synthetic_repository(tmp_path, filename)
    if operation == "modify":
        migration.write_text("revision = 'synthetic_old'\ndown_revision = 'unapproved_rewrite'\n")
    elif operation == "delete":
        migration.unlink()
    elif operation == "rename":
        migration.rename(migration.with_name("renamed_applied_revision.py"))
    else:
        migration.with_name("synthetic_new_forward_revision.py").write_text(
            "revision = 'synthetic_new'\ndown_revision = 'synthetic_old'\n"
        )
    _commit(repository, f"Synthetic {operation}")
    result = _execute_guard(repository, base, event)
    if operation == "forward-add":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "immutability holds" in result.stdout
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "ship corrections as NEW revisions" in result.stdout
        reported_path = "renamed_applied_revision.py" if operation == "rename" else filename
        assert reported_path in result.stdout


def test_actual_ci_push_guard_checks_interior_commits_not_only_tip(tmp_path: Path) -> None:
    repository, migration, base = _synthetic_repository(tmp_path, FORMERLY_EXEMPT[0])
    migration.write_text("revision = 'synthetic_old'\ndown_revision = 'unapproved_rewrite'\n")
    _commit(repository, "Interior migration rewrite")
    (repository / "README.md").write_text("Harmless tip commit.\n")
    _commit(repository, "Harmless tip after rewrite")
    result = _execute_guard(repository, base, "push")
    assert result.returncode == 1, result.stdout + result.stderr
    assert FORMERLY_EXEMPT[0] in result.stdout


def test_actual_ci_push_guard_refuses_missing_before_history(tmp_path: Path) -> None:
    repository, _, base = _synthetic_repository(tmp_path, FORMERLY_EXEMPT[0])
    (repository / "README.md").write_text("Harmless visible change.\n")
    _commit(repository, "Harmless change")
    result = _execute_guard(repository, base, "push", before="f" * 40)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "cannot verify migration immutability" in result.stdout
    assert "immutability holds" not in result.stdout
