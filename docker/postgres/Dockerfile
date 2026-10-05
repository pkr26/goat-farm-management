# Same PostgreSQL 16 / Debian trixie / glibc line as the upstream service.
# Upgrade only reviewed security package bytes; never mutate an existing volume here.
FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f AS packages
COPY docker/debian-security-packages.json docker/fetch_debian_security_packages.py /pins/
RUN python /pins/fetch_debian_security_packages.py /pins/debian-security-packages.json /fixes

FROM postgres:16@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54
COPY --from=packages /fixes /tmp/security-debs
RUN dpkg -i /tmp/security-debs/*.deb && rm -rf /tmp/security-debs
