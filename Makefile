.PHONY: sync run test lint format typecheck check migrate up down

sync:
	uv sync --frozen

run:
	uv run uvicorn bond_trading.main:app --reload

test:
	uv run pytest

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

typecheck:
	uv run mypy src

check:
	uv lock --check
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy src
	uv run pytest

migrate:
	uv run alembic upgrade head

up:
	docker compose up --build -d

down:
	docker compose down
