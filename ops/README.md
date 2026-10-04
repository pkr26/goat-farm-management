# Operations reference

The [Deployment guide](../docs/deployment.md) defines the supported topology. This
directory supplies an executable baseline for the supported single-host production
topology. It does not install or publish anything by itself.

The host needs systemd, a PostgreSQL client compatible with the server (`pg_dump`,
`pg_restore`, and `psql`), GnuPG, standard GNU userland tools, and the AWS CLI when
off-site S3 publication is enabled. The deployed backend virtual environment supplies
the Python dependencies used by the inventory capture and backup helpers. Pin these host
packages through the operating system's normal patch-management policy.

## Backup and freshness timers

Install the four unit files from [ops/systemd/](systemd/) under `/etc/systemd/system/`,
create a locked-down `goatfarm-backup` account, and make only `/var/backups/goatfarm`,
its systemd-managed state directory, and the node-exporter textfile directory writable
by it. Deploy the backend virtual environment under `/opt/goatfarm/backend/.venv` so the
scripts use the frozen Python dependencies. Import the encryption and signing keys into
`/var/lib/goatfarm-backup/gnupg` (owned by the backup account, mode `0700`); the unit
sets that writable path as `GNUPGHOME` because GnuPG needs to create lock and trust
state while its home directories are deliberately hidden. Keep only non-secret public
configuration in the unit itself. `/etc/goatfarm/backup.env` must contain the production
database/TLS/GPG/S3 settings in the [Backup and recovery
runbook](../docs/backup-recovery.md) and `GOATFARM_RECOVERY_INVENTORY_FILE`, pointing at
a fresh unbound inventory created with `backend/scripts/recovery_inventory.py capture`.
The capture used for production should include `--check-s3-versioning` and a
manifest/receipt from the independent object replica or backup.

Enable both timers after a manual successful run:

```sh
sudo systemctl enable --now goatfarm-backup.timer goatfarm-backup-freshness.timer
sudo systemctl start goatfarm-backup.service
sudo systemctl start goatfarm-backup-freshness.service
systemctl list-timers 'goatfarm-backup*'
```

The backup service fails unless PostgreSQL, the versioned screening-object recovery
point, and escrow receipts/fingerprints for JWT, TOTP, idempotency HMAC, database CA,
and backup GPG material are represented. Restore refuses a production archive without
its bound `.recovery.json` sidecar. The hourly freshness service hashes the newest
candidate, verifies its exact checksum and bound inventory, and continues past a broken
newest set only to identify the latest usable recovery point; it emits both freshness
and rejected-set metrics. The alert rules calculate age from the verified recovery
timestamp and also require the node-exporter textfile to have been refreshed within two
hours, so a stopped freshness timer cannot leave a stale green metric behind. They also
alert when either backup timer is inactive or either oneshot service fails.

## Monitoring and alert delivery

[Prometheus scrape configuration](prometheus/prometheus.scrape.example.yml) and [alert
rules](prometheus/goatfarm.rules.yml) are runnable Prometheus inputs. Replace the
example targets, enable node_exporter's systemd and textfile collectors, provide
cAdvisor and blackbox exporter, and route `critical`/`warning` labels to an
independently hosted Alertmanager receiver. Keep metrics bearer material in the
referenced file, never in the Prometheus YAML.

Acceptance testing is operational, not a configuration review: stop the API, make
`/readyz` fail, stop the screening worker, mask each backup timer/service, age the
freshness textfile beyond two hours, and age a copy of the newest backup beyond 26
hours. Record the alert receiver timestamp for each signal, then restore the services.
Perform the whole database + object + key-material recovery drill quarterly and verify
representative screening objects by the exact manifest/version recovery point recorded
in the sidecar.

Ship Docker JSON logs off-host before the local three-file rotation window. The log
collector must preserve timestamp, service/container, request ID, and the append-only
security-event signal; the repository intentionally does not embed an
environment-specific log vendor or receiver credential.
