#!/bin/sh
# Rebuild the reviewed gosu source with the security-patched, digest-pinned Go builder.
# Keep symbols for govulncheck, as required by upstream gosu's build policy.
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: build_gosu.sh <amd64|arm64> </absolute/output/path>" >&2
    exit 2
fi
architecture=$1
destination=$2
case "$architecture" in
    amd64|arm64) ;;
    *) echo "unsupported gosu architecture: $architecture" >&2; exit 2 ;;
esac
case "$destination" in
    /*) ;;
    *) echo "gosu output path must be absolute" >&2; exit 2 ;;
esac
test "$(go env GOVERSION)" = "go1.27.4"

source_commit=29b939aa5bd4dc588af765b8248f6e39150d2565
source_sha256=2b70da8a5480fafedc16bb1fc91b572f064d03df736cbd91a616af2f1b66ab51
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT HUP INT TERM
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    "https://codeload.github.com/tianon/gosu/tar.gz/$source_commit" \
    --output "$workspace/source.tar.gz"
printf '%s  %s\n' "$source_sha256" "$workspace/source.tar.gz" | sha256sum --check --strict
mkdir "$workspace/source"
tar --extract --gzip --file "$workspace/source.tar.gz" \
    --directory "$workspace/source" --strip-components=1 --no-same-owner
cd "$workspace/source"
export GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH="$architecture"
go mod download
go mod verify
mkdir -p "$(dirname "$destination")"
go build -mod=readonly -trimpath -buildvcs=false -ldflags '-d -w' -o "$destination" .
go version -m "$destination"
