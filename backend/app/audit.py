"""Structured security events on the shared ``goatfarm.audit`` logger.

Every event renders one grep/SIEM-friendly line::

    security_event event='auth.refresh.family_revoked' user_id=3 family_id=17 — …

Targets are formatted with ``repr`` so attacker-supplied values can never
contain raw newlines (log forging) — only ids, enums, and counts belong here,
never PII or secret material. The 2026-09-16 audit (DET-1/DET-2) found the
platform's strongest signals — refresh-token family revocation, JWT
verification failures, RBAC denials, document downloads — were only visible
as plain 401/403/404 status lines; this module gives them a durable,
alertable identity. ``api/team.py``'s farm-scoped ``_audit_event`` predates
this module and keeps its richer farm/actor framing; both emit the same
``security_event`` prefix so one parser covers both.
"""

from __future__ import annotations

import logging
from typing import Any

audit_log = logging.getLogger("goatfarm.audit")


def security_event(event: str, summary: str, **targets: Any) -> None:
    fields = " ".join(f"{name}={value!r}" for name, value in targets.items())
    audit_log.info("security_event event=%r %s — %s", event, fields, summary)
