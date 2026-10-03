# Final fake-data isolation probes

Both PL07 and PL02 passed on 2026-10-03. These receipts prove build-context exclusions, effective Compose interpolation, required credential-delivery presence, and mounted filesystem separation. They do **not** prove normal production service boot, valid credentials/certificates, database privileges, provider access, notification delivery, or deployment readiness.

Reproduce from the checkout root:

```sh
python3 audit_reports/implementation-2026-10-03/final-ops-probes/run_isolation_probes.py
```

The script creates random task-owned leaves under the gitignored `secrets/` directory. It never opens or enumerates existing operator secret files. Its invented fixture contents are not credentials or certificates. Shell `GOATFARM_*`, `POSTGRES_*`, and `COMPOSE_*` overrides are removed from subprocess environments so existing exported values cannot enter the rendered evidence.

PL07 uses a real `FROM scratch` build with the repository root as context and `COPY secrets/pl07-sentinel-<random>/ /probe/`. Docker rejects the source as absent or excluded by `.dockerignore` (exit 1). A separate build whose entire context contains only a fake positive-control file succeeds (exit 0), establishing that the rejection is not a broken builder. The positive-control image is subsequently removed.

PL02 resolves `docker-compose.yml` and `docker-compose.production.yml` **separately**, with exact task-owned fake env files. Both env files pass the actual `compose_env_guard.py`. The production configuration resolves three distinct, read-only `/run/secrets/app` sources. The API receives its API database file route; the migrator receives the migration database file route; the worker receives the worker database file route. Migration credentials are absent from API and worker environments, and API HMAC credentials are absent from migration and worker environments. Removing each required API DB, migration DB, worker DB, or idempotency HMAC delivery route makes the actual guard reject it (exit 2). Reusing the API directory for the worker also fails the guard (exit 2).

Three disposable containers then use the **actual resolved production `/run/secrets` bind mounts**. Each runs UID 10001 with a read-only root, no capabilities, no network, and `/bin/sh` replacing the entrypoint of an already cached `postgres:16` image. PostgreSQL is never started, and no app image is pulled or run. Each container successfully verifies that its allowed fake files are readable and that every foreign-role fake filename is absent. Only the API mount includes the fake JWT directory; migrator and worker JWT paths are absent. Each has the common fake CA file. The commands print filenames and verification status, never file contents.

All task-owned directories, containers, and image tags were removed and their absence checked. The cached base image and unrelated containers/images were preserved. Docker build cache was not globally pruned.

Evidence:

- [Exact commands, Docker/guard output, and exit codes](commands-and-results.txt)
- [Structured results, role-specific allowed/forbidden files, and cleanup checks](results.json)
- [Resolved development config containing only fake values](development-resolved-fake-config.json)
- [Resolved production config containing only fake values](production-resolved-fake-config.json)
- [Reproducible probe script](run_isolation_probes.py)

Two initial tooling failures are retained transparently: the local legacy builder does not support `--progress`, and Docker cannot bind platform-private `/var/folders` paths. Their receipts are [the unsupported CLI attempt](initial-builder-cli-unsupported.txt) and [the unshared temporary bind attempt](initial-unshared-temp-bind.txt), with cleanup recorded in their matching JSON files. The final commands use supported build options and fresh ignored workspace fixture directories. These initial failures are not counted as successful probes.
