# Read-only frontend mutation provenance review

The initial review independently inspected `frontend/mutation/mutate_identity.mjs`, the newly added support-input harness test, campaign creation/checks, transform/runner and mutation README without editing mutation files. A subsequent final source review confirmed and corrected an active-campaign artifact race; that follow-up, its FM claim map and final 22-case harness receipt are documented in `final-source-review.md`.

The current input manifest captures the presently discovered support files read by unit tests: `public/sw.js` and icons, `next.config.ts`, scripts including the translation scanner, its source-tree literal baseline, dependency lock/package/patches, Vitest configs/setup, shared OpenAPI, backend model/schema parity inputs and Docker/Compose/edge templates. Direct manifest queries returned `captured: true` for all fourteen listed support paths in `mutation-input-check.json`. No current public/Next/parity dependency omission was found that would invalidate the revised support-file provenance.

The new test creates task-owned temporary fixtures, adds each public/config/script dependency and a nested repository shared contract, then proves campaign creation rejects stale coverage. Rejection occurs before campaign/resume, so historical rows cannot be accepted through this path. Existing content modifications/deletions are also detected by file hashes/input-map changes, although this particular new test explicitly exercises additions rather than every modification/deletion case.

Focused execution from `frontend`:

```sh
node --test --test-name-pattern='service-worker, config, script and repository inputs' mutation/mutate_harness.test.mjs
```

One test passed, zero failures; duration 191.4585 ms. The root agent owns the complete harness rerun. This is a harness contract, not a fresh complete application mutation campaign or score.

One operational finding was reported to the root agent: the new recursive `../backend/app` walk initially included ignored Python bytecode. The first independent query observed 130 `.pyc` inputs among 1,184 total inputs. Python cache creation or refresh could invalidate otherwise unchanged frontend coverage/resume. This failed closed and did not fabricate a score, but unnecessarily made measurements sensitive to unrelated Python execution. The root agent subsequently excluded those transient inputs; the saved `mutation-input-check.json` records 1,054 inputs, zero bytecode files, with all fourteen required support paths still captured.

Current trees did not expose a required symlinked source input. The manifest walker does not follow symlink entries, so a future symlinked source/support file would need an explicit policy or test. Installed dependency bytes are represented by lock/package/patch provenance, not individually hashed; these contracts assume the installed dependency tree corresponds to those files. Node version is present in campaign policy. No environment contents or credentials were persisted.
