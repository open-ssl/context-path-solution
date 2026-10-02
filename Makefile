#!make

define load_env
	$(eval include .env)
	$(eval export)
endef

.PHONY: open_project run run-fg deploy build down logs test test-cov test-html-cov lint format benchmark

PROJECT_PATH ?= .
COMPOSE := docker compose -f .deployment/docker-compose.yml --project-directory .

open_project:
	$(call load_env)
	cd $(PROJECT_PATH) && uv sync --all-packages

# Bring the whole system up (infra + 4 services), building images, in the background.
run:
	$(call load_env)
	$(COMPOSE) up --build -d

# Same, but attached (logs stream in the foreground).
run-fg:
	$(call load_env)
	$(COMPOSE) up --build

# Backwards-compatible alias.
deploy: run-fg

build:
	$(call load_env)
	$(COMPOSE) build

down:
	$(call load_env)
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test:
	uv run pytest --cov

test-cov:
	uv run pytest --cov --cov-report=term-missing

test-html-cov:
	uv run --with pytest-cov pytest --cov --cov-report=html
	@echo "📊 HTML report generated at htmlcov/index.html"

lint:
	uv run ruff check .
	$(MAKE) format

format:
	@if [ ! -f .git/hooks/pre-commit ]; then \
		echo "Installing pre-commit hook..."; \
		cp dev/utils/pre-commit-hook .git/hooks/pre-commit; \
		chmod +x .git/hooks/pre-commit; \
		echo "✅ Pre-commit hook installed"; \
	fi
	@uv run poe format

benchmark:
	@uv run poe benchmark
