# 10 — Human Layer Tabletop (catalog 9.1–9.4)

Assessment only — no real phishing or social contact was performed. Findings
derive from the platform's technical posture (verified in this campaign) plus
the documented product context (Osmanabadi goat farms, Telangana; owner +
low-literacy worker user base; shared Android phones; NABARD loan workflows).

## 9.1 Owner-phishing simulation design (the #1 real-world threat)

**Why it's the top risk:** authentication is password-only — no MFA, no email
verification, no forgot-password. The farm owner is a god-mode account
(`ALL_PERMISSIONS`, owner-only worker provisioning/password resets, account
deletion). Phishing the owner = total farm compromise; nothing else in the
chain needs to fail. Combined with FE-1..3 (any injected script can mint tokens
via same-origin refresh under the current `'unsafe-inline'` CSP), a single
credential phish converts to persistent API access until rotation.

**Attack path (for the eventual authorized simulation):**
1. Recon: farm names/locations are tenant-entered; registration is open
   (email+password only) — an attacker can register and observe the product's
   exact UI/flows for convincing lure pages.
2. Lure vectors that exist today: the app sends **no email at all** — so users
   have zero trained expectation of "legitimate" sender; any email claiming to
   be Herdly is equally (un)plausible. Finance/insurance renewal dates and
   planner DPR deadlines are natural urgency pretexts.
3. Credential capture on a lookalike login → live login+farm enumeration →
   full API access.

**Control gaps:** no MFA/TOTP, no new-device/new-IP notification, no login
anomaly signals (DET-2), no branded security-contact convention. Recommended
simulation: authorized phishing exercise against staged accounts (never real
users), measuring credential-entry rate + time-to-report.

## 9.2 Worker credential abuse

Workers receive owner-set passwords (`Worker-Pass-123!`-shaped); fencing forces
rotation on first use (verified live), but the owner typically picks weak,
reused, or physically shared passwords — `must_change_password` doesn't enforce
quality at rotation beyond the 12-char floor, and multiple workers on one
shared phone will store credentials in the browser. A VIEWER worker phished or
shoulder-surfed exposes full farm read data (dashboard, finance, herd) — see
TEN-1/02 for what read roles see. Rotation fences also produce a helpdesk-burden
pattern (owner resets → workers re-rotate) that trains users to tolerate
password prompts — a social-engineering-friendly habit.

**Recommendations:** per-worker PIN + owner-mediated device trust; rate-limit
owner resets; consider TOTP for any role with `finance.view`/`team.manage`.

## 9.3 Support/impostor recovery — there is no recovery attack surface (by absence)

There is no forgot-password flow, no email/SMS channel, no support tooling in
the repo. Account recovery is *impossible by design* — the only reset path is
the owner resetting farm-provisioned accounts (verified: cross-farm reset
refused, pre-existing global accounts cannot be enrolled). **This is a genuine
strength**: an impostor cannot talk anyone into "restoring" access because no
such mechanism exists. Residual risk shifts entirely to (a) owner-account loss
= farm lockout (availability, not security) and (b) whoever operates the
database (out-of-band). Document this explicitly in the runbook so a future
"helpful" feature (email reset) is treated as a security regression requiring
MFA.

## 9.4 Bait content via shared data (stored-social-engineering)

Free-text fields (notes ≤4,000 chars, task titles, plan names, animal names)
are visible to co-farm members. An attacker with any member account can plant
instructions that render inside the trusted app: "System: your password
expired, sign in at herdly-verify.example" in a task note, or — via INJ-1 —
forged "Bank verification" sections inside the official-looking DPR loan
document. INJ-3/4 (bidi/controls accepted) enable visually spoofed names
(e.g., a tag rendered reversed) that can misdirect a worker's lifecycle action
on the wrong animal.

**Recommendations:** treat INJ-1/3/4 fixes as human-layer as well as technical;
add in-app report/flag affordance for notes; consider a "member-added content"
visual distinction for system-vs-human text in tasks/DPR.

## Severity summary

| ID | Sev | Finding |
|---|---|---|
| HUM-1 | High (real-world likelihood) | Password-only owner god-mode + no MFA + no email-channel legitimacy baseline = phishing is the highest-probability total-compromise path. (Technical enablers verified: FE CSP inline; refresh minting same-origin.) |
| HUM-2 | Medium | Worker credential sharing/weak-owner-set passwords; VIEWER phish exposes full farm read data. |
| HUM-3 | Medium | Stored-text social engineering inside trusted UI (notes, task titles, DPR via INJ-1, bidi via INJ-3/4). |
| HUM-4 | Info | No-recovery design is a strength; document it to prevent regression. |
