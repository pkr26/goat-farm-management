# Same PostgreSQL 16 / Debian trixie / glibc line as the upstream service.
# Upgrade only reviewed security package bytes; never mutate an existing volume here.
FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f AS packages
COPY docker/debian-security-packages.json docker/fetch_debian_security_packages.py /pins/
RUN python /pins/fetch_debian_security_packages.py /pins/debian-security-packages.json /fixes

# Rebuild the pinned gosu release with the patched Go standard library.
FROM --platform=$BUILDPLATFORM golang:1.27.4-trixie@sha256:b54b2639de8c1bfe538ec5ffc45f29b1932235ac5f6d63c059853646677112d9 AS gosu
ARG TARGETARCH
COPY docker/build_gosu.sh /pins/build_gosu.sh
RUN sh /pins/build_gosu.sh "$TARGETARCH" /out/gosu

FROM postgres:16@sha256:63e83b5a3dc36a917dccb76fe1bd2ac51f1ec9c1deb47ff20d4f04233bf6eae9
ENV GOSU_VERSION=1.20
COPY --from=gosu /out/gosu /usr/local/bin/gosu
COPY --from=packages /fixes /tmp/security-debs
RUN dpkg -i /tmp/security-debs/*.deb \
    && rm -rf /tmp/security-debs \
    && gosu --version \
    && gosu nobody true
