UV_CACHE_DIR ?= /tmp/saas-uv-cache

.PHONY: install db-up db-down migrate bootstrap api worker format lint typecheck test test-unit test-integration build secrets audit check

install:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --locked

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen alembic upgrade head

bootstrap:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen saas-bootstrap-demo

api:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen saas-api

worker:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen saas-worker

format:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen ruff format .

lint:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen ruff format --check .
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen ruff check .

typecheck:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen mypy src tests

test-unit:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pytest -q tests/unit

test-integration:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pytest -q tests/integration

test:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pytest -q --cov=saas_platform --cov-report=term-missing

build:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv build

secrets:
	git ls-files -z | xargs -0 env UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen detect-secrets-hook --baseline .secrets.baseline

audit:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv export --locked --no-dev --no-hashes --no-emit-project -o /tmp/saas-requirements.txt
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pip-audit --cache-dir /tmp/saas-pip-audit-cache -r /tmp/saas-requirements.txt

check: lint typecheck test build secrets
