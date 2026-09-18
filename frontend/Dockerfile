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
# CSP is set by the public edge at runtime, where a generic registry image
# can receive the deployment's actual S3/MinIO origin after validation.
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM node:24-alpine@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# The digest-pinned base is intentionally not mutated with `apk upgrade`:
# image refreshes are explicit and reproducible, while CI's Trivy gate checks
# that chosen digest. The standalone server invokes Node directly, and keeping
# npm in the runtime image adds unused package-management code and its
# transitive attack surface. Leave npm available in deps/builder, but remove
# it and its command shims from the final image.
RUN rm -rf /usr/local/lib/node_modules/npm \
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
