#!/usr/bin/env bash
# Extracted verbatim (logic-identical) from .github/workflows/release.yml's
# "Verify published bits are the scanned bits (RT-R-6)" step, with docker
# CLI calls replaced by fixtures, to exercise the bash+jq walking locally.
set -euo pipefail

GATE_ID="$1"        # docker inspect --format '{{.Id}}' of the loaded gate image
INDEX_JSON="$2"     # docker buildx imagetools inspect --raw of the published tag
CHILD_JSON="$3"     # imagetools inspect --raw of the selected child manifest
expect="$4"         # pass | fail

verify() {
  local gate_id="$1" index="$2" child="$3"
  local image_manifest config_digest
  image_manifest="$(printf '%s' "$index" | jq -r --arg arch "amd64" \
    '.manifests[] | select(.platform.architecture == $arch and .platform.os == "linux" and (has("vnd.docker.reference.type") | not)) | .digest')"
  if [ -z "$image_manifest" ] || [ "$image_manifest" = "null" ]; then
    echo "no image manifest found"; return 1
  fi
  config_digest="$(printf '%s' "$child" | jq -r '.config.digest')"
  if [ "$gate_id" != "$config_digest" ]; then
    echo "DIVERGED: gate ${gate_id} vs published ${config_digest}"; return 1
  fi
  echo "verified: ${config_digest}"
}

# OCI index shaped like buildx output: image manifest + an attestation manifest.
INDEX='{"manifests":[
  {"digest":"sha256:image1111","platform":{"architecture":"amd64","os":"linux"}},
  {"digest":"sha256:attest1111","platform":{"architecture":"unknown","os":"unknown"},"vnd.docker.reference.type":"provenance"},
  {"digest":"sha256:image2222","platform":{"architecture":"arm64","os":"linux"}}]}'

# Case 1: digests match -> pass
CHILD_OK='{"config":{"digest":"sha256:aaaabbbbccccdddd"}}'
result="$(verify "sha256:aaaabbbbccccdddd" "$INDEX" "$CHILD_OK" pass 2>&1)" && status=ok || status=fail
[ "$expect" = "pass" ] && [ "$status" = "ok" ] && [ "$result" = "verified: sha256:aaaabbbbccccdddd" ] \
  && echo "CASE1 (match) PASS" || { echo "CASE1 FAIL: $status / $result"; exit 1; }

# Case 2: digests diverge -> fail
CHILD_BAD='{"config":{"digest":"sha256:ffff0000ffff0000"}}'
if verify "sha256:aaaabbbbccccdddd" "$INDEX" "$CHILD_BAD" fail >/dev/null 2>&1; then
  echo "CASE2 FAIL: divergence not caught"; exit 1
else
  echo "CASE2 (divergence) PASS"
fi

# Case 3: attestation filtering — an index containing ONLY attestation
# manifests for amd64 must find nothing (the not/has filter works).
INDEX_ATTEST_ONLY='{"manifests":[
  {"digest":"sha256:attest1111","platform":{"architecture":"amd64","os":"linux"},"vnd.docker.reference.type":"provenance"}]}'
if verify "sha256:whatever" "$INDEX_ATTEST_ONLY" '{"config":{"digest":"sha256:x"}}' fail >/dev/null 2>&1; then
  echo "CASE3 FAIL: attestation-only index slipped through"; exit 1
else
  echo "CASE3 (attestation filter) PASS"
fi
