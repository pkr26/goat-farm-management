# Security and evidence-gate remediation

These changes address S26-01, S26-02, O26-05 and O26-06. The original audit and its reproductions remain unchanged. Test counts below overlap the integrated suite; do not add them as unique tests.

| Finding | Corrected behavior | Durable regression coverage |
|---|---|---|
| S26-01 | `backend/app/deps.py` bounds significant numeric farm-ID digits before integer conversion. An overlong selector returns HTTP 400; leading-zero representations of a legitimate ID preserve the existing authorization semantics. | `backend/tests/test_audit29_security.py`: 5,000 nines, 5,000 zeros, signed-range boundaries, many leading zeros with an owned farm and a foreign farm. |
| S26-02 | Successful exact-session and general logout revocations create an attributed `SecurityEvent` in the same database transaction. Events record user, family and revocation scope without credentials. Replayed/invalid/no-op requests create no extra events. | Exact session, password cookie, password bearer-only, PIN cookie and PIN bearer modes; old-token rejection; duplicate replay; metadata privacy; injected commit failure proves both revocation and event roll back. |
| O26-05 | The mutation gate independently derives measured verdicts and score from raw attempts. It validates complete selection, passing baseline, structured assertion receipts, target/campaign identity, selection hash and report totals. Non-finite/out-of-range scores, contradictory summaries, infra failures and partial evidence fail closed. Backend receipts must refer to the selected tests and collect the complete selection; `-x` assertion kills remain valid. | `test_ci_mutation_gate.py`: both formats, valid 80%/100%, invalid score types/ranges, false kill labels, sampled/incomplete runs, infra errors, count/hash/status contradictions and foreign receipt IDs. Real isolated pytest runner and Vitest judge/report producers are also passed through the final gate. |
| O26-06 | The SARIF gate requires nonempty recognized CodeQL runs and valid result arrays. Supplied invocation receipts must report success with no error notifications. It rejects contradictory rule IDs/indexes, malformed levels/severity and failed runs even beside a clean run. | `test_security_workflow.py`: empty/malformed/failed runs, execution/config errors, mixed directory, conflicting rule/index, invalid levels, high/error blocks and clean/lower-severity controls. |

## Evidence

- [security-targeted.log](security-targeted.log): 25 passed, including existing security-event coverage and the new HTTP/transaction controls.
- [Independent review and final gate regressions](../frontend/peer-gates-verified.log): 94 passed, including a real backend mutation producer-to-gate control.
- [Frontend scoped notes](../frontend/README.md) record the separate 22-test real Vitest harness control and integrated checks.
- The coordinating report records full-suite coverage and final static verification. Earlier incomplete/failed iteration logs are retained, including the temporary old-container-digest assertion while image pins were changing.

## Compatibility and limits

SARIF permits omission of invocation metadata. Both `CodeQL` and the documented `CodeQL command-line toolchain` driver name are accepted. If invocation metadata is present, contradictory execution evidence fails the gate. This follows the [CodeQL SARIF output documentation](https://docs.github.com/en/code-security/reference/code-scanning/codeql/codeql-cli/sarif-output) and the [SARIF 2.1.0 invocation contract](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html). A structurally valid clean report with omitted optional invocation metadata is not proof that an external scanner actually ran; the workflow controls production and artifact ownership.

The mutation gate checks the repository producers' receipt formats. It does not make locally editable receipts cryptographically authentic. Backend baseline receipts retain the producer's passing status and selection hash; frontend baselines additionally retain structured test receipts. Exact source/campaign provenance remains the responsibility of the existing producers and workflow together with this final gate.

The full 26-track audit has not been repeated as a new independent audit. This work fixes its 29 findings, adds adversarial regressions, independently reviews the changes, and runs the integrated validation described in the coordinating report.
