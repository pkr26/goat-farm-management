# Backend production image (build from the repo root):
#   docker build -t goatfarm-backend .
#   docker run -e GOATFARM_DATABASE_URL=postgresql+asyncpg://... goatfarm-backend
# The container applies migrations, then serves the API as a non-root user.
# The CMD hard-codes --workers 1 (the auth rate limiter is in-memory, per
# process — multi-worker would silently multiply every limit). Also set
# GOATFARM_ENVIRONMENT=production, GOATFARM_COOKIE_SECURE=true,
# GOATFARM_CORS_ORIGINS, GOATFARM_DB_SSLMODE=require for real deployments.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Layer order: install deps from the manifest FIRST (cached across app-only
# code changes), then copy the app source. Any change under backend/app/ no
# longer busts the pip install layer.
COPY backend/pyproject.toml backend/uv.lock ./
RUN pip install --no-cache-dir . \
    && groupadd --system --gid 10001 goatfarm \
    && useradd --system --uid 10001 --gid goatfarm goatfarm

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

USER goatfarm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

# exec so uvicorn becomes PID 1 (SIGTERM from Docker/K8s reaches it directly
# and --timeout-graceful-shutdown 30 drains in-flight requests).
# --workers 1 is required (see file header).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-graceful-shutdown 30"]
