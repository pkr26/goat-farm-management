"""Collect a passing isolated baseline and atomically publish coverage provenance."""

import argparse
import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mutate_identity import atomic_json, database_admin_sha256, input_identity, sha_bytes
from mutate_run import BACKEND, VENV_PY, snapshot_ignore


def discover_test_modules(
    workspace: Path, environment: dict[str, str]
) -> tuple[list[Path], dict[str, Any]]:
    """Let pytest recursively choose files under its actual collection policy."""
    inventory_path = workspace / ".coverage-collection.json"
    collect_env = environment | {"MUTATION_COVERAGE_INVENTORY_PATH": str(inventory_path)}
    result = subprocess.run(
        [
            str(VENV_PY),
            "-m",
            "pytest",
            "tests",
            "--collect-only",
            "-q",
            "-p",
            "mutation.coverage_inventory",
            "--color=no",
        ],
        cwd=workspace,
        env=collect_env,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        inventory = json.loads(inventory_path.read_bytes())
        items = inventory["items"]
        if (
            result.returncode != 0
            or inventory["exit_code"] != 0
            or not isinstance(items, list)
            or not items
        ):
            raise ValueError("pytest collection failed or found no test cases")
        files: set[Path] = set()
        nodes: set[str] = set()
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("nodeid"), str)
                or not isinstance(item.get("file"), str)
            ):
                raise ValueError("malformed pytest collection item")
            path = (workspace / item["file"]).resolve()
            if (
                not path.is_relative_to((workspace / "tests").resolve())
                or not path.is_file()
                or item["nodeid"] in nodes
            ):
                raise ValueError("duplicate node or collection file outside tests")
            files.add(path)
            nodes.add(item["nodeid"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(
            f"complete pytest collection failed; no coverage published: {exc}"
        ) from exc
    return sorted(files), inventory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.dry_run:
        print(json.dumps({"selection": "all tests", "isolated": True, "writes": False}))
        return
    inputs = input_identity(BACKEND)
    with tempfile.TemporaryDirectory(prefix="herdly-coverage-") as temporary:
        root = Path(temporary) / "repo"
        shutil.copytree(BACKEND.parent, root, ignore=snapshot_ignore)
        workspace = (root / BACKEND.name).resolve()
        if input_identity(workspace) != inputs:
            raise SystemExit("source/tests changed during snapshot; no coverage published")
        env = os.environ.copy()
        env.update(
            {
                "GOATFARM_TEST_DB": f"herdly_cov_{uuid.uuid4().hex[:12]}_test",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workspace),
                "COVERAGE_FILE": str(workspace / ".coverage-mut"),
                # Operator scripts use python3 when the isolated checkout has
                # no .venv directory. Resolve it to the same pinned interpreter.
                "PATH": str(VENV_PY.parent) + os.pathsep + env.get("PATH", ""),
            }
        )
        collection_inventory: dict[str, Any] | None = None
        baseline_databases = [env["GOATFARM_TEST_DB"]]
        if args.workers == 1:
            result = subprocess.run(
                [
                    str(VENV_PY),
                    "-m",
                    "pytest",
                    "tests",
                    "--maxfail=1",
                    "--cov=app",
                    "--cov-context=test",
                    "--cov-report=",
                ],
                cwd=workspace,
                env=env,
                check=False,
            )
            passed = result.returncode == 0
        else:
            modules, collection_inventory = discover_test_modules(workspace, env)
            full_items = collection_inventory["items"]
            groups: list[list[Path]] = [[] for _ in range(min(args.workers, len(modules)))]
            weights = [0] * len(groups)
            baseline_databases = [f"herdly_cov_{uuid.uuid4().hex[:12]}_test" for _ in groups]
            # File size is a cheap deterministic proxy for test work. Each group
            # executes its complete modules, never a sample of test cases.
            for module in sorted(modules, key=lambda p: -p.stat().st_size):
                slot = min(range(len(groups)), key=lambda i: weights[i])
                groups[slot].append(module)
                weights[slot] += module.stat().st_size

            def collect(pair: tuple[int, list[Path]]) -> bool:
                index, selection = pair
                group_env = env.copy()
                group_env["GOATFARM_TEST_DB"] = baseline_databases[index]
                group_env["COVERAGE_FILE"] = str(workspace / f".coverage-mut.shard-{index}")
                inventory_path = workspace / f".coverage-collection.shard-{index}.json"
                group_env["MUTATION_COVERAGE_INVENTORY_PATH"] = str(inventory_path)
                log = BACKEND / "mutation" / f"baseline-shard-{index}.log"
                with log.open("w") as output:
                    result = subprocess.run(
                        [
                            str(VENV_PY),
                            "-m",
                            "pytest",
                            "-p",
                            "mutation.coverage_inventory",
                            *[str(p) for p in selection],
                            "--maxfail=1",
                            "--cov=app",
                            "--cov-context=test",
                            "--cov-report=",
                            "--cov-fail-under=0",
                        ],
                        cwd=workspace,
                        env=group_env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                inventory_matches = False
                try:
                    observed = json.loads(inventory_path.read_bytes())
                    selected_files = {path.relative_to(workspace).as_posix() for path in selection}
                    expected_items = [item for item in full_items if item["file"] in selected_files]
                    inventory_matches = observed.get("exit_code") == 0 and sorted(
                        observed.get("items", []), key=lambda item: item["nodeid"]
                    ) == sorted(expected_items, key=lambda item: item["nodeid"])
                except (OSError, ValueError, KeyError, TypeError):
                    inventory_matches = False
                print(
                    f"baseline shard {index}: exit {result.returncode}; "
                    f"inventory {inventory_matches}",
                    flush=True,
                )
                return result.returncode == 0 and inventory_matches

            with ThreadPoolExecutor(max_workers=len(groups)) as pool:
                passed = all(list(pool.map(collect, enumerate(groups))))
            if passed:
                import coverage

                merged = coverage.Coverage(
                    data_file=str(workspace / ".coverage-mut"),
                    config_file=str(workspace / "pyproject.toml")
                    if (workspace / "pyproject.toml").exists()
                    else True,
                )
                merged.combine(
                    data_paths=[
                        str(workspace / f".coverage-mut.shard-{i}") for i in range(len(groups))
                    ],
                    strict=True,
                )
                merged.save()
                from coverage.results import should_fail_under

                total = merged.report(file=io.StringIO())
                passed = not should_fail_under(
                    total, merged.config.fail_under, merged.config.precision
                )
                print(
                    f"merged baseline coverage: {total:.2f}% (floor {merged.config.fail_under})",
                    flush=True,
                )
        if not passed or not (workspace / ".coverage-mut").is_file():
            raise SystemExit("clean coverage baseline failed; no coverage published")
        if input_identity(BACKEND) != inputs:
            raise SystemExit("source/tests changed during baseline; no coverage published")
        coverage_bytes = (workspace / ".coverage-mut").read_bytes()
        temporary_data = BACKEND / ".coverage-mut.tmp"
        temporary_data.write_bytes(coverage_bytes)
        temporary_data.replace(BACKEND / ".coverage-mut")
        atomic_json(
            BACKEND / ".coverage-mut.provenance.json",
            {
                "schema": 2,
                "source_root": str(workspace.resolve()),
                "inputs": inputs,
                "coverage_sha256": sha_bytes(coverage_bytes),
                "test_database_admin_sha256": database_admin_sha256(env),
                "baseline_exit_code": 0,
                "selection": "all tests",
                "baseline_workers": args.workers,
                "baseline_databases": baseline_databases,
                **(
                    {"collection_inventory": collection_inventory}
                    if collection_inventory is not None
                    else {}
                ),
            },
        )


if __name__ == "__main__":
    main()
