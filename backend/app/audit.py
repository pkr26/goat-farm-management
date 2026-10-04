"""Transactional security events plus bounded failure-signal projection.

Every event renders one grep/SIEM-friendly line::

    security_event event='auth.refresh.family_revoked' user_id=3 family_id=17 — …

Targets are formatted with ``repr`` so attacker-supplied values can never
contain raw newlines (log forging) — only ids, enums, and counts belong here,
never PII or secret material. The 2026-09-16 audit (DET-1/DET-2) found the
platform's strongest signals — refresh-token family revocation, JWT
verification failures, RBAC denials, document downloads — were only visible
as plain 401/403/404 status lines; this module gives them an alertable
identity. Authenticated state transitions are durable; untrusted failure
traffic is aggregated in fixed-cardinality process memory so an attacker
cannot turn novel credentials into append-only database or log writes.
``api/team.py``'s farm-scoped ``_audit_event`` predates this module and keeps
its richer farm/actor framing; both durable paths emit the same
``security_event`` prefix so one parser covers them.
"""

from __future__ import annotations

import logging
from collections import Counter
from threading import Lock
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .models.security_events import SecurityEvent

audit_log = logging.getLogger("goatfarm.audit")
_PENDING_PROJECTIONS = "goatfarm.security_event_projections"

type TransientSecuritySignal = Literal[
    "auth.refresh.invalid",
    "auth.token.invalid",
    "auth.token.version_mismatch",
    "auth.worker_pin.login_failed",
    "auth.totp.challenge_failed",
    "auth.totp.disable_failed",
    "auth.totp.secret_unavailable",
    "rbac.denied",
]

_TRANSIENT_SIGNAL_SUMMARIES: dict[TransientSecuritySignal, str] = {
    "auth.refresh.invalid": "invalid or replayed refresh credentials rejected",
    "auth.token.invalid": "access token verification failures (expired tokens excluded)",
    "auth.token.version_mismatch": "revoked-generation access tokens presented",
    "auth.worker_pin.login_failed": "wrong or unknown worker PIN attempts",
    "auth.totp.challenge_failed": "TOTP challenge authentication failures",
    "auth.totp.disable_failed": "wrong TOTP codes on disable attempts",
    "auth.totp.secret_unavailable": "stored TOTP secrets could not be authenticated",
    "rbac.denied": "authenticated requests missing a required permission",
}
_transient_signal_counts: Counter[TransientSecuritySignal] = Counter()
_transient_signal_lock = Lock()


def _emit_projection(event_name: str, summary: str, targets: dict[str, Any]) -> None:
    fields = " ".join(f"{name}={value!r}" for name, value in targets.items())
    audit_log.info("security_event event=%r %s — %s", event_name, fields, summary)


@event.listens_for(Session, "after_commit")
def _project_committed_security_events(session: Session) -> None:
    """Emit only committed mutation events; the DB row remains authoritative."""
    pending = session.info.pop(_PENDING_PROJECTIONS, [])
    for event_name, summary, targets in pending:
        _emit_projection(event_name, summary, targets)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_security_events(session: Session) -> None:
    session.info.pop(_PENDING_PROJECTIONS, None)


def note_transient_security_signal(event_name: TransientSecuritySignal) -> None:
    """Count hostile failure traffic without allocating per-attacker state.

    The event-name allowlist fixes cardinality at compile time and runtime.
    One opening line is emitted for the whole aggregation interval; every
    later request only increments one integer until the periodic summary
    drains it. This keeps the signal visible without letting unique bearer
    tokens force database commits or one log write per request.
    """
    if event_name not in _TRANSIENT_SIGNAL_SUMMARIES:
        raise ValueError(f"Unsupported transient security signal: {event_name}")
    with _transient_signal_lock:
        opened = _transient_signal_counts[event_name] == 0
        _transient_signal_counts[event_name] += 1
    if opened:
        audit_log.warning(
            "security_signal_started event=%r aggregation='process-memory' — %s",
            event_name,
            _TRANSIENT_SIGNAL_SUMMARIES[event_name],
        )


def drain_transient_security_signals() -> dict[TransientSecuritySignal, int]:
    """Atomically snapshot and clear the fixed-cardinality signal counters."""
    with _transient_signal_lock:
        snapshot = dict(_transient_signal_counts)
        _transient_signal_counts.clear()
    return snapshot


def emit_transient_security_signal_summary(
    interval_seconds: int,
) -> dict[TransientSecuritySignal, int]:
    """Emit at most one summary line per supported signal and interval."""
    summary = drain_transient_security_signals()
    for event_name, count in sorted(summary.items()):
        audit_log.warning(
            "security_signal_summary event=%r count=%d window_seconds=%d "
            "persistence='non-durable' — %s",
            event_name,
            count,
            interval_seconds,
            _TRANSIENT_SIGNAL_SUMMARIES[event_name],
        )
    return summary


def security_event(
    event: str,
    summary: str,
    *,
    session: AsyncSession,
    **targets: Any,
) -> None:
    """Queue an append-only row in ``session`` and emit its log projection.

    The transaction containing the state change is mandatory, making the
    event and mutation atomic. Repeatable failure traffic belongs in
    ``note_transient_security_signal`` instead of this append-only table.
    """
    durable_targets = dict(targets)
    session.add(SecurityEvent(event=event, summary=summary, targets=durable_targets))
    session.sync_session.info.setdefault(_PENDING_PROJECTIONS, []).append(
        (event, summary, durable_targets)
    )
