# Contributing

This is a proprietary project; a pull request does not grant a license to use
or distribute the project. By submitting a contribution, you represent that
you have the right to submit it and agree that it may be incorporated under
the project's existing license terms.

Keep changes narrowly scoped, add regression tests, and run the backend,
frontend, migration, and deployment-artifact checks relevant to the change.
Never use an existing development or production database for migration tests:
set both `GOATFARM_DATABASE_URL` and `GOATFARM_MIGRATION_DATABASE_URL` to an
explicitly named disposable `_test` database and remove it afterward.

Use private vulnerability reporting from SECURITY.md for security defects.
Do not place secrets, personal data, animal-health records, screenshots of
production, or unredacted database dumps in issues, commits, or CI artifacts.
Maintainers named by CODEOWNERS review changes; operational and clinical
changes also require an owner competent in that domain.
