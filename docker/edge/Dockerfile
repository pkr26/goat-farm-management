# Immutable official base plus reviewed Alpine 3.24 package fixes.
# Refreshed against signed package indexes on 2026-11-13; built, scanned,
# signed, and published with the two application images.
FROM nginx:1.30.6-alpine3.24@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a

RUN apk add --no-cache --upgrade \
      'libexpat=2.10.0-r0' \
      'libxslt=1.1.43-r4' \
      'pcre2=10.49-r0' \
    && apk cache clean
