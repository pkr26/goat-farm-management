# Contributing

This is a proprietary project. A pull request does not grant a license to use or
distribute it. By submitting a contribution, you represent that you have the right to
submit it and agree that it may be incorporated under the project's existing [license
terms](LICENSE).

## Development workflow

Read the [Development guide](docs/development.md) for setup and checks, the
[Architecture guide](docs/architecture.md) for module boundaries, and
[frontend/AGENTS.md](frontend/AGENTS.md) for frontend conventions.

Keep changes focused. Use clear domain names, explain non-obvious constraints in
comments, and remove obsolete code when its callers are removed. Preserve public API
contracts, authorization checks and persisted data formats unless the change explicitly
requires a migration or compatibility transition.

Run `make check` and the tests relevant to the change. Add regression coverage for
behavior changes. Regenerate both OpenAPI and the frontend client after changing API
routes or schemas; commit the generated diff with the source change. Applied Alembic
revisions are immutable: append a new revision for a correction, and rehearse the
supported upgrade path.

Never use an existing development or production database for migration tests. Set both
`GOATFARM_DATABASE_URL` and `GOATFARM_MIGRATION_DATABASE_URL` to an explicitly named
disposable `_test` database and remove it afterward. Backend pytest fixtures recreate
their own guarded disposable database; read the [Testing
instructions](docs/development.md#testing) before running them.

## Review and sensitive data

Describe the problem, resulting behavior and validation in the pull request. Call out
migration, operational or clinical effects that a reviewer needs to assess. Maintainers
named by [CODEOWNERS](.github/CODEOWNERS) review changes; operational and clinical
changes also need a reviewer competent in that domain.

Use [private vulnerability reporting](SECURITY.md#private-reporting) for security
defects. Keep secrets, personal data, animal-health records, production screenshots and
unredacted database dumps out of issues, commits and CI artifacts.
