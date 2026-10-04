# Security policy

## Supported versions

Security fixes are applied to the current `main` branch and the most recent
published release. Older releases should be upgraded rather than operated
indefinitely.

## Private reporting

Do not open a public issue for a suspected vulnerability or include production
data, credentials, animal-health records, or exploit details in public logs.
Use the repository's [private vulnerability reporting
form](https://github.com/pkr26/goat-farm-management/security/advisories/new).
Include the affected version/digest, impact, reproduction conditions, and a
safe contact method. Maintainers will acknowledge a complete report as soon as
practical, coordinate validation/remediation privately, and publish an
advisory when users have an actionable fix.

If GitHub private reporting is unavailable, contact the repository owner
through the private channel established for your deployment. There is no
public emergency credential in this repository.

## Release authenticity

The release workflow keyless-signs each backend, frontend, and edge OCI
manifest and the release's `SHA256SUMS` file. Verify an image by exact digest:

```sh
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/pkr26/goat-farm-management/.github/workflows/release.yml@refs/tags/v[^/]+$' \
  ghcr.io/pkr26/goatfarm-backend@sha256:RELEASE_DIGEST
```

Verify downloaded SBOM checksums with the attached Sigstore bundle, then the
checksums themselves:

```sh
cosign verify-blob \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/pkr26/goat-farm-management/.github/workflows/release.yml@refs/tags/v[^/]+$' \
  --bundle SHA256SUMS.sigstore.json SHA256SUMS
sha256sum --check SHA256SUMS
```

Versioned release tags and assets are immutable by policy. Changed artifacts
must use a new version; treat any replacement under an existing version as a
distribution-channel incident.
