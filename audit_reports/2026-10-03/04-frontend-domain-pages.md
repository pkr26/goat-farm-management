# Independent frontend domain page audit

Workspace: `/Users/pradeepreddy/Desktop/goat-farm-management-main`.
Read-only application audit; no application changes. `git status --short` was empty after probes were removed. Read `frontend/AGENTS.md` before review. Existing audit comments and tests were treated as navigation/fixtures, not evidence that a bug exists or was fixed.

## Confirmed findings

### DP1 — P2: An old insurance write dismisses a newer insurance dialog and loses its draft

**Primary location:** `frontend/src/app/(app)/finance/insurance/page.tsx:204–207`.
**Supporting locations:** Add dismissal at 220–227; renewal completion at 485–489 and dismissal at 503–506; claim completion at 595–598 and dismissal at 615–619; parent conditional mounts and unconditional close callbacks at 1047–1068; opener buttons at 850, 943, 1005.

AddPolicyDialog, RenewPolicyDialog and ClaimPolicyDialog can all be dismissed with Escape/the shared close button/backdrop while their POST is pending. That unmounts the dialog and its local useSingleFlight guard. The parent retains no pending flag or dialog-attempt id, so it permits immediately opening another instance. A successful old request checks only the farm epoch and invokes its old onClose callback, which clears the parent's current creating/renewing/claiming state. On the same farm it therefore closes the new instance and discards everything entered in it. The old dialog's local mounted flag in useSingleFlight guards only setPending; it does not suppress the action continuation or parent callbacks.

**Observed reproduction:** fill policy A, submit with its response deferred, press Escape, reopen Register policy, type `NEW-DRAFT` in the new Policy number input, then release A's successful response. The second dialog disappears without having been submitted. The same source mechanism is present in Renew and Claim (those variants were source-verified, not separately exercised). Reopening also allows a distinct second write during the first because each mounted instance has its own single-flight guard; the directly verified impact is draft loss, not a claim that all concurrent payloads bypass transport idempotency.

**Fix direction:** lift pending state and session ownership to the page, keep pending mutations dismissible if desired, and let an old completion refresh/toast its result while closing only the dialog session that initiated it. An instance-local flight or farm-only fence cannot provide session ownership.

### DP2 — P2: Phenotype controls accept edits during a save, then silently discard the newer values

**Primary locations:** `frontend/src/app/(app)/animals/[id]/page.tsx:423` and `:438`.
**Supporting locations:** payload capture 381–386; unconditional success close 388–392; pending dismissal lock 411–413; Save/Cancel buttons 455–467.

EditPhenotypeDialog disables its Save and Cancel actions and prevents dismissal while the shared actionFlight is pending, but leaves both Base UI Select controls enabled. save() has already captured coatColor and horned from the click's render and sent them in the PATCH. The operator can then choose different visible values; the response still saves the old values and the continuation closes the dialog with a success toast, dropping the edits the operator currently sees.

**Observed reproduction:** render a black, non-horned animal; click Save with PATCH deferred; select Spotted while that request is pending. The dialog visibly says Spotted, but the request body remains `{coat_color:"black",horned:false}`. On successful response the dialog closes. No second PATCH is sent.

**Fix direction:** disable both selects (or their fieldset) while actionFlight.pending/profileSettling, as the lifecycle forms elsewhere already do; alternatively retain and explicitly save dirty changes after the captured request.

### DP3 — P2/P3: Same-route finance URL navigation applies the new filters with the previous ledger offset

**Primary location:** `frontend/src/app/(app)/finance/page.tsx:626–632`.
**Supporting locations:** independent offset state at 637; outbound request includes offset at 678–687; local filter handlers explicitly reset at 984, 995, 1017; the empty state at 1045 and pager at 1268–1275.

The URL reconciliation resets month/type/category on a changed query string but leaves offset untouched. Next can reuse this page on a query-only navigation, so a user on page 2 of an unfiltered ledger who follows/restores a narrower month/category URL starts that new filter at offset 50. If only two records match, the page shows no transactions even though both exist. Unlike feeding/health/kidding, Finance has no effect rehoming an out-of-range offset. The pager remains visible, so users can recover manually with Previous; this limits severity but does not make the URL faithfully open its requested ledger slice.

**Observed reproduction:** load an unfiltered total of 100, click Next, then rerender the same page with `month=2026-01` and a server total of 2. The new request is `month=2026-01&...offset=50`. No automatic offset-0 request follows.

**Fix direction:** reset offset during external URL filter adoption, or encode/adopt pagination in the URL and recover out-of-range offsets. Preserve local page turns if adding the reset, since ordinary page changes do not currently write offset to this URL.

### DP4 — P3: Two Record/Add health event links redirect to the log without opening the form

**Primary locations:** `frontend/src/app/(app)/animals/[id]/page.tsx:2097` and `frontend/src/app/(app)/health/page.tsx:1280`.
**Supporting locations:** redirect in `health/new/page.tsx:20–24`; health dialog hydration in `health/page.tsx:869–890`; `withReturnTo` in `lib/permission-navigation.ts:175–178`.

The animal profile's empty health-history Add event CTA points to `/health/new?returnTo=/animals/<id>` without an `animal_id`. The empty health log's Record health event CTA points to bare `/health/new`. That route shim only replaces `/health/new` with `/health` and preserves its query; it supplies no create-dialog intent. The destination initializes `open` to false and its URL effect returns without opening unless `task_id`, `animal_id`, or `purchase_batch_id` is present. Both CTAs therefore land on the log with the form closed. The animal-profile CTA also drops the animal it was meant to record against. The user must press the separate page-header Add event button and manually choose the animal, while the original action appears to have done nothing useful.

**Evidence:** complete source trace of both links, the redirect, and the dialog initialization/effect. This fourth finding was not exercised through a runtime probe. The schedule page's equivalent CTA correctly passes `animal_id` at `health/schedule/[animalId]/page.tsx:151–154`, which confirms the supported current-source deep-link contract.

**Fix direction:** pass the current animal id from the profile, and provide a recognized create-dialog intent for unscoped `/health/new` navigation (or have the log's empty-state CTA open its local dialog directly).

## Verification

A temporary targeted Vitest file exercised the current, unmodified page components through the repository's real AuthProvider/React Query providers and MSW transport. The three runtime reproductions (DP1–DP3) passed as assertions of the observed bad behavior:

```
./node_modules/.bin/vitest run src/test/independent-domain-audit-probe.test.tsx
Test Files 1 passed (1)
Tests 3 passed (3)
Duration 1.48s
```

The temporary repository test was removed after the run. A reviewable copy remains at `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-frontend-domain-pages-probe.test.tsx` (copy back into frontend/src/test to run with the existing Vitest configuration). No full suites were run. These probes establish UI request/lifecycle behavior; they do not assert production database state.

## Coverage and limits

The 13 main assigned domain pages total 17,383 lines. All 13 were inventoried with rg/wc and received complete visible source reads in nontruncated chunks, including the original eight sampled pages after the parent requested continuation. This means complete source exposure, not a claim that every branch was dynamically exercised or that the pages are bug-free. The animals/new, breeding ultrasound redirect, kidding/new redirect, health/new redirect, health schedule, and health/task-prefill auxiliary source files were also read completely. Supporting libraries/backend contracts/test fixtures have separate full-versus-sampled classifications in `/Users/pradeepreddy/Desktop/goat-farm-management-main/audit_reports/2026-10-03/evidence/herdly-frontend-domain-pages-files.txt`; test files were used only as fixtures/harness, not comprehensively audited.

Additional source checks ruled out a proposed manage-without-view dashboard dead end because the backend enforces PERMISSION_DEPENDENCIES. The duplicated stock/ration dialogs across mobile/desktop have instance-local locks, but the transport performs body-based in-flight coalescing; this was not promoted to a finding without an isolated user-visible reproduction. Legacy AI breeding records can render a nullable buck link, but no current creation path was demonstrated, so that was also not promoted. Existing parent findings concerning offline queues/login/tablet onboarding and known backend clinical duty/photo behavior are deliberately absent from this report.
