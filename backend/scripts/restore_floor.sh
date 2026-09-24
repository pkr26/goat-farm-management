#!/usr/bin/env bash
# Fail-closed restore floor (INFRA-3, corrected 2026-09-17).
#
# Alembic revision ids are random hex: their lexicographic order has NO
# relation to migration chain order, so a revision "floor" must be decided by
# chain membership, never by string comparison. This helper owns that
# decision for restore.sh: a backup may be restored only when its stamped
# revision is one of the known revisions at-or-after f4e5f6a7b8c9 (the
# idempotency-fingerprint purge — anything older still carries unkeyed
# fingerprints of password-bearing worker-create bodies, an offline guessing
# oracle once the HMAC secret is also known).
#
# Usage: restore_floor.sh <revision>   → exit 0 = allowed, exit 1 = refused
# (the reason and operator guidance are printed to stderr).
#
# MAINTENANCE: RESTORE_ALLOWED_REVISIONS is the migration graph reachable
# from the floor to the current head, in topological order. Append every new
# branch or merge revision after each of its parents. The graph-sync test
# (tests/test_deployment_artifacts.py) rebuilds Alembic's DAG and fails on
# any drift, so a migration that forgets this list cannot merge.

set -euo pipefail

RESTORE_FLOOR_REVISION=f4e5f6a7b8c9

RESTORE_ALLOWED_REVISIONS=(
    "f4e5f6a7b8c9"
    "a1b2c3d4e5f7"
    "b2c3d4e5f6a8"
    "c4d5e6f7a8b9"
    "d1c2b3a4e5f6"
    "e3f4a5b6c7d9"
    "a6c9e2f4b7d1"
    "b7c8d9e0f1a2"
    "c2a4e6b8d013"
    "d3b5f7c9e024"
    "e6f8a0b2c4d7"
    "b1c2d3e4f5a6"
    "c3d4e5f6a7b1"
    "d5e7f9a1b3c4"
    "e7f9a1b3c5d8"
    "b3d7f1a5c9e2"
    "c5a8e1f3b7d2"
    "f8a2c4e6b1d9"
    "d1e2f3a4b5c6"
    "e3a5b7c9d1f2"
    "b5d7f9a1c3e5"
    "f9b3c7d1e5a2"
    "a1b2c3d4e5f6"
    "bd201c1cdc1b"
    "e8b0d2f4a6c1"
    "b6d8f0a2c4e6"
    "c4f6a8b0d2e5"
    "f1e2d3c4b5a6"
    "a7b8c9d0e1f2"
    "b8c9d0e1f2a3"
    "c9d0e1f2a3b4"
    "d0e1f2a3b4c5"
    "e1f2a3b4c5d6"
    "f2a3b4c5d6e7"
    "b7c1d5e9f3a2"
    "c8d2e6f0a4b3"
    "d9e3f7a1b5c4"
    "e0f4a8b2c6d5"
    "a19b2569d466"
    "a7c8d9e0f1b2"
    "b8d9e0f2a3c4"
    "c9e0f1a3b4d5"
    "d0f1a2b3c4d6"
    "c3e5a9f1d7b4"
    "a6d4e2f9c8b7"
    "c4d8e1f9a2b7"
    "b7e8f9a0c1d2"
    "f7a9c1e3b5d7"
    "b9c0d1e2f3a4"
    "cad1e2f3a4b5"
    "b1c3d5e7f9a2"
    "c3d5e7f9a1b3"
    "d4e6f8a0b2c4"
    "e5a7c9d1b3f5"
    "f6b8d0e2a4c6"
    "a8c0e2f4b6d8"
    "b9d1f3a5c7e9"
    "c3d4e5f6a7b8"
)

revision="${1:-}"

if [[ ! "${revision}" =~ ^[0-9a-f]{12}$ ]]; then
    echo "Refusing restore: backup carries no valid Alembic revision marker" >&2
    exit 1
fi

is_allowed() {
    local candidate
    for candidate in "${RESTORE_ALLOWED_REVISIONS[@]}"; do
        if [[ "${candidate}" == "${revision}" ]]; then
            return 0
        fi
    done
    return 1
}

# Bash 3.2 (stock macOS) has no negative array indices.
restore_head_revision="${RESTORE_ALLOWED_REVISIONS[${#RESTORE_ALLOWED_REVISIONS[@]}-1]}"

if ! is_allowed; then
    # Random-hex ids make "below the floor" indistinguishable from "newer
    # than this script knows" without embedding the whole chain, so give the
    # operator both exits.
    echo "Refusing restore: backup revision ${revision} is not in the" >&2
    echo "restore-allowed set (chain floor ${RESTORE_FLOOR_REVISION}, current" >&2
    echo "head ${restore_head_revision})." >&2
    echo "Either the backup predates ${RESTORE_FLOOR_REVISION} (it still carries" >&2
    echo "unkeyed idempotency password fingerprints — restore it to a scratch" >&2
    echo "database, run alembic upgrade head to re-purge, then dump/restore" >&2
    echo "that) or it is newer than these scripts (update" >&2
    echo "backend/scripts/restore_floor.sh from the current migration chain)." >&2
    exit 1
fi

exit 0
