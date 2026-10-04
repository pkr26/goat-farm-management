# Field workflows

[Documentation index](README.md) · [Project overview](../README.md)

## Notifications

`GOATFARM_NOTIFICATIONS_ENABLED=true` turns on the minute-tick dispatcher in the API
process: a per-farm morning digest (each opted-in recipient gets their task-scope duties
for the day) plus same-day alerts — vet-confirmed screening findings (hooked into the
review endpoint), the kidding-watch head count, duties overdue by at least three days and feed items
under reorder level. Provider adapters: `console` (default, logs) and `msg91` (SMS;
production refuses to boot enabled without its auth key). Every delivery passes
day-dedupe (one attempt per farm/recipient/class/payload/local-day, recorded in the
append-only `notification_log`), quiet hours (21:00–06:00 farm-local) and a per-farm
daily cap. Clinical screening and movement alerts enter a durable outbox in the same
transaction as their domain change. Quiet hours and caps defer these events; cross-day
recipient dedupe prevents a deferred event becoming a new paid attempt. A crashed
dispatcher has a five-minute lease before recovery. Outbox, digest and periodic-alert
schedules run independently so a slow class cannot block the others. Provider work is
bounded by `GOATFARM_NOTIFICATIONS_DELIVERY_CONCURRENCY` globally and
`GOATFARM_NOTIFICATIONS_PER_FARM_DELIVERY_CONCURRENCY` per farm; database claim
transactions end before network I/O and settlements use fresh short transactions.
SENDING and FAILED attempts with possible provider acceptance remain settled for
operator review; unknown SMS outcomes are not automatically sent again. External
delivery is not guaranteed exactly once. Recipient phones and per-class opt-ins are
managed by the farm owner via `GET/PUT /api/team/workers/{id}/notifications` —
notifications are never an account-recovery channel.

## Worker tablet app

`/worker` provides the field-worker workflow: a Telugu-first PWA (installable on the
farm tablet's home screen) with tap-your-name + PIN sign-in, the day's duties as large
cards, and a transactional IndexedDB outbox. Each manual completion or skip is committed
on the device before its first request and retains one `Idempotency-Key` across retries.
The UI acknowledges saving only after storage commits. Storage/capacity failures refuse
the new action while preserving existing work. Successful replies become receipts;
conflicts and other definitive rejections remain visible for review. Operations older
than 72 hours require review and are never silently expired. PIN model:

- One-time setup runs on the tablet: `/worker/login` offers "Set up this tablet", where
  a manager/owner signs in (password + TOTP or recovery code) and picks the farm to pin
  (`herdly.tabletFarm`). Pinning ends the temporary, cookie-free manager session
  immediately; exact-family revocation leaves the manager's sessions on other devices
  intact. Cancelled and late setup requests cannot install a manager identity on the
  tablet.
- PINs are per-membership Argon2 credentials, provisioned/reset by the farm owner only
  (`POST /api/team/workers` with `pin`, `POST /api/team/workers/{id}/reset-pin`);
  rotation revokes every session. Exactly one credential per worker: a password
  (force-rotated via the must-change fence) or a tablet PIN — never both; a PIN-only
  worker's web password is an unguessable random hash, so the web login door stays shut.
  Production requires the full 12-digit PIN length; shorter legacy PINs are rejected at
  sign-in until an owner rotates them.
- A PIN never bypasses the second factor (TOTP-active accounts get 403) and never admits
  an owner (owners hold no membership). Login-grade throttling: per (IP, farm,
  membership) plus a farm-wide spray scope at 10×.
- `GET /api/auth/worker-roster?farm_id=…` is deliberately unauthenticated so the
  tablet's first screen works without a session. Tradeoff: the display names of
  PIN-enabled workers are enumerable per farm id — names only, never emails or roles (a
  worker provisioned without a name is listed as "Worker \<membership_id\>"),
  hard-throttled per IP (30/5 min). The roster is off by default:
  `GOATFARM_WORKER_ROSTER_ENABLED=false` makes the endpoint answer 404 before any roster
  row is read. A provisioned shared tablet deployment must explicitly opt in with
  `true`. The tablet sign-in flow depends on the roster (its tap-to-sign-in screen has
  no manual id entry).
- Shared-device discipline: "End shift" removes credentials, views and the active cached
  shift while preserving unresolved actor/farm-scoped actions. They are visible and
  replayable only after the original worker signs in to the same farm. A fresh live
  session and farm epoch are checked before every replay. Multiple tabs share
  transactional writes and a replay lease.
- An online, authorized board may save a minimal, credential-free snapshot for an
  ongoing 12-hour shift. `/worker/offline` can reopen it in the same browser tab after a
  connectivity-loss reload; it grants no API authority. Cached manual-duty actions are
  saved as untrusted drafts and are never replayed automatically. The original worker
  must sign in to the same farm, inspect each draft, and explicitly confirm it before
  delivery. Linked forms need connectivity. End shift or loss of the per-tab marker
  closes the cached view without deleting already-saved work.
- Not implemented: worker-native simplified forms, Background Sync/push, QR badges. PIN
  sessions are restricted to their issuing membership and farm.

For session and permission rules, see [Authentication](authentication.md). For the photo
capture and veterinary review flow, see [Screening](screening.md).
