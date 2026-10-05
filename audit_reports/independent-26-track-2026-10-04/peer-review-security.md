# Independent challenge review of root security findings

Reviewed current source and the newly authored `evidence/root/test_security_probes.py`; no historical audit conclusions were consulted. This is a second-agent review, not a second real-database run. The root's owned-database HTTP receipts remain the runtime evidence for both routes. Additional independent pure-Python boundary evidence is saved as `evidence/frontend/peer-security-probe.log`.

## Oversized numeric X-Farm-Id — accepted, Low

`backend/app/deps.py:716–720` checks ASCII numeric spelling, then calls `int(x_farm_id)` before its numeric range guard. An all-digit value above Python's integer-string conversion limit therefore raises an unhandled ValueError instead of the intended 400. The new HTTP probe uses a valid bearer, ASGITransport with exception propagation disabled, and verifies a 500 for 5,000 digits; its setup is appropriate for distinguishing a real HTTP error response from a test exception.

Independent challenge: using the repository's Python 3.13.15 virtual environment, I checked the actual integer-string limit (4,300), regex acceptance, and conversion of 19/4,300/4,301/5,000 digits. The first two parse and reach the numeric rejection; the latter two raise before range validation. There is no global `set_int_max_str_digits` override in application source. This corroborates the causal boundary.

Severity remains Low: this requires a valid authenticated request and creates an avoidable input-triggered server error. It does not bypass farm authorization or grant access. The single-request probe does not establish resource exhaustion or denial of service; do not characterize it that way. A production outer proxy could reject some oversized headers first, so the ASGI evidence alone is not proof of every deployed edge configuration's response. The application boundary is still incorrect and should validate canonical length/range before conversion or catch conversion failure.

## Successful logout omits append-only SecurityEvent — accepted as a Low observability/contract gap

I traced both `backend/app/api/auth.py:1611` and `:1641` through their success/commit paths and through `backend/app/deps.py:347–379` (`revoke_user_sessions`, `revoke_session_family`). Neither the endpoints nor these helpers call `security_event`. The helper updates refresh-session state but does not create a generic audit row. `backend/app/audit.py:121–139` explicitly requires callers to enqueue SecurityEvent; its after-commit callback projects already-enqueued records and cannot synthesize a missing logout record.

The requirement is current and explicit: `docs/architecture.md:78–82` promises successful security state changes in an append-only ledger, in the same transaction, and names refresh-family revocation as an example. The root probe measures SecurityEvent count before/after successful logout, also verifies the old bearer receives 401, and parameterizes both routes. Thus it confirms working revocation with an absent durable SecurityEvent, rather than confusing a failed/no-op logout with missing logging.

Challenge/qualification: revocation itself **is durable** in RefreshSession/User state, and ordinary request logging can leave other traces. The correct wording is “no append-only SecurityEvent for successful logout,” not “logout is unauditable,” “no durable state,” or “tokens remain valid.” No access-control bypass is shown. The fix should log only an actual state change with actor/family/scope context in the same transaction, retaining idempotence and avoiding per-request rows for invalid, duplicate or already-revoked logout traffic. If maintainers intend ordinary logout to be excluded from the ledger, narrow the architecture promise explicitly; as written, the current implementation does not satisfy it.

## Result

Both root findings survive independent challenge at Low severity with the qualifications above. No additional security defect was introduced by this review. No new database was created or accessed by this reviewer.
