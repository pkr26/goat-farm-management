# Independent review of S26-01, S26-02, O26-05 and O26-06 remediation

Reviewed the current header parser, both logout handlers and new database regressions; mutation gate and both real mutation producers/reporters; SARIF gate, CodeQL workflow assumptions and existing/new tests. This review did not alter backend application code or the original audit evidence.

- **S26-01:** Significant-digit bounding precedes `int`, preserves the existing leading-zero contract, and retains range/membership enforcement. The new HTTP tests include pathological inputs, an authorized zero-padded selector, and a denied foreign-farm control. No additional correctness problem identified.
- **S26-02:** Events are queued in the same transaction as a proven revocation/generation change. Invalid/no-op requests do not append rows. Exact-session and legacy logout paths retain their existing locks/generation checks; attribution records IDs/scope rather than tokens or PINs. New tests cover standard/PIN, cookie/bearer, replay and transaction rollback. No additional correctness problem identified.
- **O26-05:** Independently reproduced a remaining gap: a raw pytest receipt naming an unrelated test could still support the declared complete-selection kill. Fixed by requiring the collected count to match the complete node-ID selection and every receipt node ID to belong to it. A survivor also requires outcomes for every selected test. Legitimate `pytest -x` kills remain valid without reports from later unrun selected tests; parameterized node IDs remain exact strings. Added four malformed receipt controls, a legitimate fail-fast control and a partial survivor rejection.
- **O26-06:** Independently reproduced a remaining gap: `ruleId` naming a high-severity rule plus `ruleIndex` pointing at a low-severity rule passed. The gate now rejects conflicting identity, invalid indices/IDs and malformed result/default levels. Matching ID/index and index-only records remain accepted. Added negative and positive controls.

The initial synthetic reproductions are retained in [before](peer-review-gate-probes.json); the same probes after correction are retained in [after](peer-review-gate-probes-after.json). They model malformed/contradictory evidence and do not claim the normal producers emit those contradictions.

To check real producer compatibility rather than only handwritten JSON, the existing isolated backend and frontend harness tests now pass actual runner/judge records plus their production report summarizers to the final mutation gate. These real assertion-kill receipts pass. No full-project mutation campaign was represented or run here.

Validation:

- **94 Python tests passed**, 4.19s: mutation-gate contracts, security-workflow/SARIF contracts and the real concurrent-backend-runner producer→gate control. [Log](peer-gates-verified.log).
- **22 frontend mutation harness tests passed**, 18.49s, including actual Vitest assertion kills, failing baseline/infrastructure/timeout controls and genuine producer→gate acceptance. [Log](producer-gate-node-harness.log).
- Ruff formatting/checks passed using the repository's `backend/pyproject.toml` configuration; targeted mypy passed for all five changed Python files. [Mypy receipt](peer-gates-mypy.log).

All temporary fixture projects are owned and cleaned by their harnesses. These peer-review runs did not touch the development database or start application services.
