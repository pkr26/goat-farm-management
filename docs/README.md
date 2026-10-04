# Documentation

[Project overview and quick start](../README.md)

These guides describe the current application and supported deployment. Settings,
lockfiles and workflow configuration remain the source of truth for exact defaults and
dependency versions. Historical plans and review evidence are indexed in [the
archive](archive/README.md).

## Developing the application

| Guide | Purpose |
| --- | --- |
| [Development](development.md) | Local setup, isolated testing, generated code and CI checks |
| [Architecture](architecture.md) | Repository structure and module responsibilities |
| [Domain model](domain.md) | Production stages, business rules and husbandry defaults |
| [Authentication and tenancy](authentication.md) | Session lifecycle, permissions and idempotent requests |
| [Field workflows](field-workflows.md) | Tablet setup, offline action handling and notifications |
| [Photo screening](screening.md) | Image processing, provider calls and veterinary review |

## Operating a deployment

| Runbook | Purpose |
| --- | --- |
| [Configuration](configuration.md) | Environment variables and production safeguards |
| [Deployment](deployment.md) | Single-host topology, secrets, proxies and rollout |
| [Database migrations](migrations.md) | Upgrade and downgrade boundaries, locking and preflights |
| [Backup and recovery](backup-recovery.md) | Signed recovery sets, verification and restore drills |
| [Security operations](security-operations.md) | Operator recovery and secret rotation |
| [Data retention](data-retention.md) | Verified object erasure, retry recovery and identity retention |
| [Operations baseline](../ops/README.md) | systemd timers, Prometheus inputs and alert acceptance |
| [Live screening contract](../ops/SCREENING_LIVE_CONTRACT.md) | Sandbox verification of external providers and object storage |

[Contributing](../CONTRIBUTING.md) · [Security reporting](../SECURITY.md) ·
[License](../LICENSE)
