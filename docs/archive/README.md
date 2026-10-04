# Historical audits and implementation plans

These documents record past reviews, measurements, and implementation work.
Their counts, scores, plans, and release status describe the recorded checkout.
Use the [project README](../../README.md) and current development and operations
guides for today's commands and behavior.

| Document | Historical purpose |
| --- | --- |
| [Improvement plan](APP_IMPROVEMENT_PLAN.md) | Findings, acceptance criteria, and implementation sequence |
| [Audit and implementation playbook](AUDIT_AND_IMPLEMENTATION_PLAYBOOK.md) | Earlier review and owner-operator priorities |
| [September audit](AUDIT_REPORT_2026-09-28.md) | Review findings and remediation evidence |
| [Implementation status](IMPLEMENTATION_STATUS.md) | Recorded verification and rollout boundaries |
| [Mutation testing report](MUTATION_TESTING_REPORT.md) | Historical campaigns; see current harness documentation before measuring |
| [Frontend campaign](frontend-mutation/CAMPAIGN.md) and [equivalence notes](frontend-mutation/EQUIVALENTS.md) | Earlier frontend mutation investigation and survivor triage |

Detailed evidence remains in [audit_reports](../../audit_reports/README.md) and
[the October audit](AUDIT_2026-10-01/00-MASTER.md). Existing evidence bundles are
retained intact because their manifests bind the recorded files and source
hashes. Historical source paths refer to the checkout at the time of the run.

Generated mutation manifests, coverage maps, attempts, and reports are local
outputs. Regenerate them for the current source rather than relying on old
measurements. See the [backend](../../backend/mutation/README.md) and
[frontend](../../frontend/mutation/README.md) harness guides.
