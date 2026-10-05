# Exact amd64 child of committed index, for classic local builder.
# Immutable official base plus the first Alpine 3.24 package fixes for
# CVE-2026-93990 and CVE-2026-103111. The derived edge is built, scanned,
# signed, and published with the two application images.
FROM nginx:1.30.5-alpine3.24@sha256:8f84ed99befc3891b8f329c5c202785278a2cfb7c25107d57fb2a134a3117433

RUN apk add --no-cache --upgrade \
      'libexpat=2.8.5-r0' \
      'pcre2=10.49-r0' \
    && apk cache clean
