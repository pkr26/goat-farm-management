# Screening live sandbox contract

`.github/workflows/screening-live-contract.yml` is an optional scheduled and
manual probe of the two external screening boundaries that hermetic pull-request
tests cannot cover: the configured S3-compatible object store and the configured
vision-model provider API. It never runs on a pull request or push.

Create a protected GitHub Environment named `screening-contract` with a sandbox
bucket and cost-capped provider credentials. Set the environment variable
`SCREENING_CONTRACT_ENABLED=true` only after the values below are complete.
When the opt-in is absent or false, the workflow records an explicit skipped
report without resolving adapters. Once enabled, missing credentials,
authentication failures, storage round-trip failures, provider response-schema
drift, and incomplete permanent cleanup all fail the run.

Environment variables (non-secret):

- `GOATFARM_S3_ENDPOINT_URL` (empty for AWS), `GOATFARM_S3_REGION`
- `GOATFARM_SCREENING_PROVIDER` (`anthropic` or `openai_compatible`)
- the selected provider's base URL and model variable

Environment secrets:

- `GOATFARM_S3_BUCKET`, `GOATFARM_S3_ACCESS_KEY_ID`,
  `GOATFARM_S3_SECRET_ACCESS_KEY`
- the selected provider API key
- optionally `GOATFARM_SCREENING_PROVIDER_ROTATION`, using the same JSON shape
  as `backend/.env.example`; when set, every configured provider is checked

The workflow uploads a generated 64×64 solid JPEG, HEADs and downloads the same
immutable version through `ScreeningStorage`, calls each real provider adapter
through the bounded gate schema, then invokes version-aware permanent deletion
and verifies the key is no longer readable. The retained JSON contains only the
revision, timestamp, generated-fixture digest, storage region/endpoint, and
provider name/model/endpoint/prompt-version plus bounded verdict metadata. It
never contains the bucket, object key, credentials, prompt, image encoding, or
raw model response. Keep the environment sandbox-only and apply provider-side
spend limits. Each run makes one gate invocation per configured provider; the
production adapter may make its single bounded retry after a transient failure.
