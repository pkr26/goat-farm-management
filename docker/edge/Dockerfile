# Immutable official base plus the first Alpine 3.24 package fixes for
# CVE-2026-93990 and CVE-2026-103111. The derived edge is built, scanned,
# signed, and published with the two application images.
FROM nginx:1.30.5-alpine3.24@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94

RUN apk add --no-cache --upgrade \
      'libexpat=2.8.5-r0' \
      'pcre2=10.49-r0' \
    && apk cache clean
