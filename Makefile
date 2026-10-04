.DEFAULT_GOAL := help

.PHONY: help install check lint typecheck test test-backend test-frontend build

help:
	@printf '%s\n' \
	  'install        Install both packages from their lockfiles' \
	  'check          Run formatting, lint, and type checks' \
	  'test           Run backend and frontend tests (requires PostgreSQL)' \
	  'test-backend   Run pytest against its disposable test database' \
	  'test-frontend  Run Vitest' \
	  'build          Build the frontend for production'

install:
	cd backend && uv sync --locked --extra dev
	pnpm --dir frontend install --frozen-lockfile

check: lint typecheck

lint:
	cd backend && uv run --locked --extra dev ruff format --check --config pyproject.toml . ../.github/scripts
	cd backend && uv run --locked --extra dev ruff check --config pyproject.toml . ../.github/scripts
	pnpm --dir frontend lint

typecheck:
	cd backend && uv run --locked --extra dev mypy --strict app scripts mutation ../.github/scripts
	cd backend && uv run --locked --extra dev mypy --strict tests
	pnpm --dir frontend typecheck

test: test-backend test-frontend

test-backend:
	cd backend && uv run --locked --extra dev pytest

test-frontend:
	pnpm --dir frontend test

build:
	pnpm --dir frontend build
