# 06 — Live Runtime Attacks (cross-tenant matrix, smuggling, DoS, rate limits)

The dynamic-testing half of the campaign: real HTTP attacks against a running
uvicorn + migrated Postgres. Evidence: `evidence/phase{2,4,5,6,7,8}_*.json`,
`evidence/uvicorn_runtime.log`.

## 1. Cross-tenant live matrix (catalog 2.1-live)

**50 attacks, 50 uniform 404s, zero leakage** — see report 02 for the table.
Additionally: own-farm foreign-ID probing (9 attacks) → 404/422 only; 12
X-Farm-Id/Authorization header-abuse variants all rejected (400/401) or
harmlessly canonicalized.

## 2. Request smuggling vs uvicorn/h11 (catalog 6.1, backend hop)

Raw-socket primitives (`evidence/phase6_smuggling.json`):

| Primitive | Result |
|---|---|
| CL.CL conflicting | 400 |
| CL.TE (chunked after CL) | 400 |
| TE.TE obfuscated (dup TE) | 400 |
| `Transfer-Encoding : chunked` (space obfuscation) | 400 |
| Negative Content-Length | 400 |
| CL with space-padded digits | 400 |
| Chunk-extension trailer smuggling (GET smuggled in trailer) | single response, no desync (401) |
| HTTP/1.0 + TE | 422 — parsed, no second response |
| Space in request line | 400 |
| Absolute-form target (`GET http://host/api/...`) | **200 — accepted** (RT-1, Info) |

**RT-1 (Info)**: absolute-form request targets are accepted by uvicorn. Harmless
on a direct origin server; relevant only if an outer cache/proxy normalizes
absolute-form differently (cache-key confusion). The shipped edge re-serializes
requests (buffering on, per-request upstream connections), so this cannot be
reached through it.

Edge nginx + Next-proxy hops could not be exercised live (no Docker/Node on the
audit host); static analysis (report 08 §1) concludes the shipped topology is
structurally immune: nginx buffers and re-serializes every request, uses
per-request upstream connections (no keepalive → no reuse desync), no H2
listener, and `/api/*` never transits Next in the shipped topology.

## 3. Resource exhaustion / DoS-lite (catalog 7.1–7.4)

| Probe | Result |
|---|---|
| 1 MiB+1 login body | 413 in 0.0 s |
| **Chunked** 1.25 MiB body (cap bypass attempt) | **413 after streaming** — streamed-byte counter works |
| 9 KB request target | 414 |
| 7 KB target (under cap) | passes cap, 422 validation |
| Max heavy simulation (240-month horizon, 2000 MC runs, optimization on) | 200 in 27.1 s (single run) |
| 5 concurrent heavy runs (same user) | **all instant 429** (per-user lock + CPU budget; no slot starvation) |
| Heavy run on farm B while farm A idle | 200 in 0.6 s (locks don't cross-block farms) |

## 4. Rate limiting / credential stuffing (catalog 1.1-live)

- Same-email wrong-password spray: 10×401 → 429 (composite ledger, exact cap).
- **Ledger-key normalization**: `SPRAY1…`, ` spray1… `, mixed-case variants all
  land on the same blocked key — no case/whitespace fragmentation bypass.
- Distributed single-attempt stuffing across fresh emails: under limits
  (documented ceiling ~30 guesses/5 min/email across IPs, each paying Argon2).
- Register limiter engaged after ~10 registrations from one IP (429 both the
  duplicate and fresh attempts — enumeration timing inconclusive this run;
  duplicate-email 400 remains a documented accepted tradeoff).
- Unknown-email rejection latency ~26 ms consistently (dummy-hash equalization).

## 5. DPR download (catalog 3.3-live)

See INJ-1 (report 03). Raw-socket capture confirms: static
`Content-Disposition: attachment; filename="dpr-1.md"` (no header injection),
payload + CRLF injected verbatim into the markdown body.
