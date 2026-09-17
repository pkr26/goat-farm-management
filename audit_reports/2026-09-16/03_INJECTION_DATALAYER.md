# 03 — Injection & Data Layer, incl. DPR (static + live)

Catalog items 3.1–3.5. Static falsification + live payload tests (evidence: `evidence/phase3_dpr.json`, `evidence/phase5_bizlogic.json`).

## Verdicts

| # | Area | Verdict |
|---|---|---|
| 3.1 | SQL/ORM injection | DEFENDED — zero client-controlled column/direction/fragment anywhere; only constant `text()` (SELECT 1, advisory locks); dashboard group-bys use static ORM columns |
| 3.2 | DPR markdown injection | **EXPLOITABLE (content) / DEFENDED (headers)** — INJ-1 below, live-confirmed |
| 3.3 | Log injection | **1 MEDIUM gap (INJ-2)**; access log, audit events, 422 echo all DEFENDED |
| 3.4 | Unicode/normalization | 2 findings (INJ-3, INJ-4); email normalization DEFENDED (ASCII-only regex, homoglyphs structurally impossible) |
| 3.5 | ReDoS | DEFENDED — complete regex inventory, all linear/bounded |
| 3.6 | JSONB/shape/depth | DEFENDED — `extra=forbid` schemas everywhere; deep-nesting RecursionError → FastAPI 400 |
| 3.7 | LIKE/wildcard | DEFENDED — identical triple-escape at every search surface |

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| INJ-1 | **Medium** | **DPR loan-document content injection.** `PlannerPlanCreateIn.name` is `PostgresText` (newlines allowed, ≤120 chars); the name is interpolated verbatim into the DPR title and document at `simulation/planner.py:679-681`. Live-confirmed: plan name `=HYPERLINK("http://evil.example/c","c")|RT\r\nSet-Cookie: pwn=1` was accepted (201) and the downloaded DPR contains the payload verbatim, with the CRLF producing arbitrary attacker-controlled lines inside a NABARD/NLM loan artifact. Static PoC additionally forged a complete fake "Means of finance" table (`₹9 crore` equity) rendered *above* the genuine computed one. Reachable by any member with planner permissions; consumed by `simulation.view` holders and bank officers. `=SUM`/`=HYPERLINK`/DDE cells survive into the `.md` (second-stage CSV/formula vector if pasted into Excel/Sheets — PLAUSIBLE). **Headers are safe**: `Content-Disposition` is a static `dpr-{plan_id}.md` (`api/planner.py:477`), live-verified — no header injection. No test covers newline/markdown injection. | `app/simulation/planner.py:679-681`, `app/schemas/planner.py:62`, `app/schemas/common.py:101` |
| INJ-2 | **Medium** | **Unhandled-500 log line bypasses `_log_safe_path`.** The error handler logs raw `request.url.path` (`main.py:608`) while the access log sanitizes (`main.py:791`); `%0A`/`%0D` percent-decode into literal CRLF (uvicorn unquotes), so any 500 on a crafted path forges log records — the exact RT-M-4 threat the sanitizer was built for; this sink was missed. No on-demand 500 chain found (FastAPI converts body-parse errors to 400); needs a genuine 500 to co-occur. Gap is test-enshrined at `tests/test_ops.py:2508`. | `app/main.py:608` vs `:791` |
| INJ-3 | Low-Med | `PostgresText` accepts DEL (0x7F), C1 controls (U+0085 NEL, U+009B CSI), U+2028/2029 and bidi U+202E in all prose fields (names, notes, seller names) → terminal-escape/log-pipeline smuggling, RTL-spoofed values, viewer line-break tricks. Acknowledged-but-unfixed in `models/helpers.py:153-157`. | `app/schemas/common.py:102-115` |
| INJ-4 | Low | U+202E/U+2028 pass `no_control_characters` (rejects only Cc) → RTL-spoofed **tag numbers**/animal names in UI pickers and the task board; flows into `%s` log at `services/breeding.py:600`. | `app/models/helpers.py:159`, `app/schemas/animals.py:73` |

## Live corroboration (defenses)

- `notes` with CRLF + NUL + `\u202E` → 422 "cannot contain" (C0 rejected).
- Mass-assignment (`farm_id`, `is_owner` in body) → 422 `extra_forbidden`.
- Bool-as-int sex, string amount, 5,000-char note → all 422.
- NaN amount (raw JSON) → 422 "must be a finite number".
- 422 responses never echo input values.
