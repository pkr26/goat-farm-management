# Production Next.js server. Build context is frontend/.
FROM node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS builder
WORKDIR /app
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=${BACKEND_URL}
# Build-time CSP additions for the cross-origin S3 presigned traffic of the
# disease-screening walkthrough (see src/lib/csp-origins.ts). Empty default:
# the production CSP stays exactly 'self'/data: until an operator opts in.
ARG GOATFARM_CSP_CONNECT_ORIGINS=
ARG GOATFARM_CSP_IMG_ORIGINS=
ENV GOATFARM_CSP_CONNECT_ORIGINS=${GOATFARM_CSP_CONNECT_ORIGINS} \
    GOATFARM_CSP_IMG_ORIGINS=${GOATFARM_CSP_IMG_ORIGINS}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# CVE sweep at build time: the digest-pinned base above can predate the
# latest Alpine security point releases (libcrypto3/libssl3); `apk upgrade`
# applies them so the Trivy fixable HIGH/CRITICAL gate stays at zero between
# base-image republishes. The standalone server invokes Node directly, and
# keeping npm in the runtime image adds unused package-management code and
# its transitive attack surface — the pinned base has shipped fixable
# high/critical CVEs under this exact global npm tree. Leave npm available
# in deps/builder, but remove it and its command shims from the final image.
RUN apk upgrade --no-cache \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx
RUN addgroup -S -g 10001 nextjs \
    && adduser -S -u 10001 -G nextjs nextjs
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
# Probe the local /healthz route handler (no auth, no backend dependency)
# instead of /login: a liveness check must not fail while the API is down,
# and must not pull the full login page just to prove the server is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD wget --quiet --tries=1 --spider http://localhost:3000/healthz || exit 1
CMD ["node", "server.js"]
