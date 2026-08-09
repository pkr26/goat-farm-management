# Backend production image (build from the repo root):
#   docker build -t goatfarm-backend .
#   docker run -e GOATFARM_DATABASE_URL=postgresql+asyncpg://... goatfarm-backend
# Migrations run as a separate release job; this image serves the API as a
# non-root user and can also be invoked with `alembic upgrade head` by that job.
# The CMD hard-codes --workers 1 (the auth rate limiter is in-memory, per
# process — multi-worker would silently multiply every limit). Also set
# GOATFARM_ENVIRONMENT=production, GOATFARM_COOKIE_SECURE=true,
# GOATFARM_CORS_ORIGINS, GOATFARM_DB_SSLMODE=verify-full and
# GOATFARM_MIN_PASSWORD_LENGTH>=12 for real deployments. Production also
# requires a stable RS256 keypair mounted at /app/keys (or configured paths).

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Layer order: install deps from the manifest FIRST (cached across app-only
# code changes), then copy the app source. Any change under backend/app/ no
# longer busts the pip install layer.
COPY backend/pyproject.toml backend/uv.lock ./
# `pip install .` resolves pyproject ranges and ignores uv.lock. Install the
# pinned uv client, then require the committed lock (including artifact
# hashes) without development tools or the not-yet-copied local project.
RUN pip install --no-cache-dir uv==0.12.1 \
    && uv sync --frozen --no-dev --no-install-project \
    && groupadd --system --gid 10001 goatfarm \
    && useradd --system --uid 10001 --gid goatfarm goatfarm \
    && install -d -o goatfarm -g goatfarm -m 0700 /app/keys

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts/healthcheck.py ./healthcheck.py

USER goatfarm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python healthcheck.py

# exec so uvicorn becomes PID 1 (SIGTERM from Docker/K8s reaches it directly
# and --timeout-graceful-shutdown 30 drains in-flight requests).
# --workers 1 is required (see file header). Uvicorn's implicit forwarded-
# header trust is disabled; app.main owns the explicit proxy allowlist.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "30", "--no-proxy-headers", "--no-server-header"]
