.PHONY: install dev api web check check-backend check-web test test-component test-e2e audit build clean live-doctor live-claude live-tavily

install:
	uv sync --frozen --all-extras --dev
	cd web && npm ci

dev:
	uv run relay serve --reload

api:
	uv run relay serve

web:
	cd web && npm run dev

check: check-backend check-web

check-backend:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src
	uv run pytest --cov=relay --cov-branch --cov-report=term-missing

check-web:
	cd web && npm run lint
	cd web && npm run typecheck
	cd web && npm run build

test:
	uv run pytest

test-component:
	cd web && npm run test:component

test-e2e:
	cd web && npm run test:e2e

audit:
	uv run bandit -q -r src
	uv run pip-audit
	cd web && npm audit --audit-level=high

build:
	cd web && npm run build
	uv build

clean:
	uv run relay demo reset

live-doctor:
	uv run relay live doctor

live-claude:
	uv run relay live claude --confirm RUN_LIVE_CLAUDE

live-tavily:
	uv run relay live tavily --confirm RUN_LIVE_TAVILY
