# Backend production image (build from the repo root):
#   docker build -t goatfarm-backend .
#   docker run -e GOATFARM_DATABASE_URL=postgresql+asyncpg://... goatfarm-backend
# The container applies migrations, then serves the API as a non-root user.
# Run ONE replica/worker (the auth rate limiter is in-memory, per process) and
# set GOATFARM_ENVIRONMENT=production, GOATFARM_COOKIE_SECURE=true,
# GOATFARM_CORS_ORIGINS, GOATFARM_DB_SSLMODE=require for real deployments.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./

RUN pip install --no-cache-dir . \
    && groupadd --system --gid 10001 goatfarm \
    && useradd --system --uid 10001 --gid goatfarm goatfarm

USER goatfarm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
