#!make

define load_env
	$(eval include .env)
	$(eval export)
endef

.PHONY: open_project deploy build test lint format

PROJECT_PATH ?= .
COMPOSE := docker compose -f .deployment/docker-compose.yml --project-directory .

open_project:
	$(call load_env)
	cd $(PROJECT_PATH) && uv sync --all-packages

deploy:
	$(call load_env)
	$(COMPOSE) up --build

build:
	$(call load_env)
	$(COMPOSE) build

test:
	uv run pytest

test-cov:
	uv run --with pytest-cov pytest --cov --cov-report=term-missing

test-html-cov:
	uv run --with pytest-cov pytest --cov --cov-report=html
	@echo "📊 HTML report generated at htmlcov/index.html"

lint:
	uv run ruff check .

format:
	@if [ ! -f .git/hooks/pre-commit ]; then \
		echo "Installing pre-commit hook..."; \
		cp dev/utils/pre-commit-hook .git/hooks/pre-commit; \
		chmod +x .git/hooks/pre-commit; \
		echo "✅ Pre-commit hook installed"; \
	fi
	@uv run poe format
