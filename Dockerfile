# Backend runtime image; build with `docker build -t goatfarm-backend .`.
# Migrations run separately with GOATFARM_MIGRATION_DATABASE_URL.
# Use one API worker: the in-memory auth limiter is process-local.
# Production requires secure cookies, verify-full database TLS, configured
# CORS origins, a stable RS256 keypair, and the production settings validator.
# Explicit key paths avoid the non-root user's unwritable-home fallback.
# Development can generate keys in the owned directory; production mounts
# supply them. GOATFARM_JWT_*_PATH overrides remain supported.

FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    GOATFARM_JWT_PRIVATE_KEY_PATH=/app/keys/jwt_private.pem \
    GOATFARM_JWT_PUBLIC_KEY_PATH=/app/keys/jwt_public.pem

WORKDIR /app

# Do not run a mutable distro upgrade here. The base image digest is the
# reproducibility/security boundary; refresh it deliberately (and let Trivy
# gate it) instead of producing different application images from the same
# source and Dockerfile on different build dates.

# Reviewed security update omitted by the upstream image: exact signed-index
# package URL, version, size and SHA-256 pins for both supported architectures.
COPY docker/debian-security-packages.json docker/fetch_debian_security_packages.py /tmp/security-fixes/
RUN python /tmp/security-fixes/fetch_debian_security_packages.py \
        /tmp/security-fixes/debian-security-packages.json /tmp/security-debs --package libpcre2-8-0 \
    && dpkg -i /tmp/security-debs/*.deb \
    && rm -rf /tmp/security-fixes /tmp/security-debs

# Layer order: install deps from the manifest FIRST (cached across app-only
# code changes), then copy the app source. Any change under backend/app/ no
# longer busts the pip install layer.
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/pins/uv.txt ./pins/uv.txt
# `pip install .` resolves pyproject ranges and ignores uv.lock. Install the
# hash-pinned uv client, then require the committed lock (including artifact
# hashes) without development tools or the not-yet-copied local project.
# `--no-cache` avoids shipping a wheel cache alongside the installed packages.
RUN pip install --no-cache-dir --require-hashes -r pins/uv.txt \
    && uv sync --locked --no-dev --no-install-project --no-cache \
    && groupadd --system --gid 10001 goatfarm \
    && useradd --system --uid 10001 --gid goatfarm goatfarm \
    && install -d -o goatfarm -g goatfarm -m 0700 /app/keys

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts/healthcheck.py ./healthcheck.py
COPY backend/scripts/screening_worker_healthcheck.py ./screening_worker_healthcheck.py
COPY backend/scripts/compose_env_guard.py ./scripts/compose_env_guard.py
COPY backend/scripts/rekey_totp_secrets.py ./scripts/rekey_totp_secrets.py
COPY backend/scripts/totp_breakglass.py ./scripts/totp_breakglass.py

# pip and uv are build-time tools: the runtime executes only /app/.venv.
# pip vendors its own dependency copies (msgpack, setuptools, requests, …)
# that pip itself cannot upgrade, so the base image's pip pins a vendored
# msgpack/setuptools pair with open CVEs. Strip both tools — and with them
# the entire vendored-CVE surface — from the final image (the same
# rationale as removing the global npm tree from the frontend image).
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip \
        /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
        /usr/local/bin/uv /usr/local/bin/uvx

USER goatfarm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python healthcheck.py

# exec so uvicorn becomes PID 1 (SIGTERM from Docker/K8s reaches it directly
# and --timeout-graceful-shutdown 30 drains in-flight requests).
# --workers 1 is required (see file header). Uvicorn's implicit forwarded-
# header trust is disabled; app.main owns the explicit proxy allowlist.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "30", "--no-proxy-headers", "--no-server-header"]
