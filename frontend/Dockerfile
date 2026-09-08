# Production Next.js server. Build context is frontend/.
FROM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS builder
WORKDIR /app
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=${BACKEND_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# The standalone server invokes Node directly. Keeping npm in the runtime
# image adds unused package-management code and its transitive attack surface;
# the pinned base currently ships fixable high/critical CVEs under this exact
# global npm tree. Leave npm available in deps/builder, but remove it and its
# command shims from the final image.
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
