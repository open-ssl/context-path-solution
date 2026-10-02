#!make

define load_env
	$(eval include .env)
	$(eval export)
endef

.PHONY: open_project run run-fg deploy build down logs test test-cov test-html-cov \
        lint format benchmark gen-data gen-run check-docker

PROJECT_PATH ?= .
COMPOSE := docker compose -f docker-compose.yml --project-directory .

# ---------------------------------------------------------------------------
# Environment detection (evaluated once at parse time).
#   HAS_UV : non-empty when both `uv` and pyproject.toml are present -> prefer `uv run poe`.
#   PY     : path to python3 (or python) for the no-uv fallback, empty if neither exists.
# These let every task degrade gracefully on a checker's machine without uv/poe.
# ---------------------------------------------------------------------------
HAS_UV := $(shell command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ] && echo 1)
PY := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)

INSTALL_UV := Install uv: https://docs.astral.sh/uv/getting-started/installation/
INSTALL_DOCKER := Install Docker: https://docs.docker.com/get-docker/

# Fail early with a clear message when Docker is missing (compose-backed targets).
check-docker:
	@command -v docker >/dev/null 2>&1 || { \
		echo "ERROR: Docker is required for this target. $(INSTALL_DOCKER)"; exit 1; }

open_project:
	@if [ -n "$(HAS_UV)" ]; then \
		cd $(PROJECT_PATH) && uv sync --all-packages; \
	else \
		echo "ERROR: 'uv' is required to sync the workspace. $(INSTALL_UV)"; exit 1; \
	fi

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

build: check-docker
	$(call load_env)
	$(COMPOSE) build

down: check-docker
	$(call load_env)
	$(COMPOSE) down

logs: check-docker
	$(COMPOSE) logs -f

test:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run pytest --cov; \
	elif [ -n "$(PY)" ] && $(PY) -c "import pytest" >/dev/null 2>&1; then \
		echo "uv not found — running bare pytest ($(PY))"; \
		$(PY) -m pytest; \
	else \
		echo "ERROR: need uv or a python with pytest. $(INSTALL_UV) (or: pip install pytest pytest-asyncio)"; exit 1; \
	fi

test-cov:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run pytest --cov --cov-report=term-missing; \
	elif [ -n "$(PY)" ] && $(PY) -c "import pytest" >/dev/null 2>&1; then \
		echo "uv not found — running bare pytest ($(PY))"; \
		$(PY) -m pytest; \
	else \
		echo "ERROR: need uv or a python with pytest. $(INSTALL_UV) (or: pip install pytest pytest-asyncio pytest-cov)"; exit 1; \
	fi

test-html-cov:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run --with pytest-cov pytest --cov --cov-report=html; \
		echo "📊 HTML report generated at htmlcov/index.html"; \
	else \
		echo "ERROR: 'uv' is required for the HTML coverage report. $(INSTALL_UV)"; exit 1; \
	fi

lint:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run ruff check .; \
	elif command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		echo "ERROR: need uv or ruff to lint. $(INSTALL_UV) (or: pip install ruff)"; exit 1; \
	fi
	@$(MAKE) format

format:
	@if [ -d .git ] && [ ! -f .git/hooks/pre-commit ]; then \
		echo "Installing pre-commit hook..."; \
		cp dev/utils/pre-commit-hook .git/hooks/pre-commit; \
		chmod +x .git/hooks/pre-commit; \
		echo "✅ Pre-commit hook installed"; \
	fi
	@if [ -n "$(HAS_UV)" ]; then \
		uv run poe format; \
	elif command -v ruff >/dev/null 2>&1; then \
		ruff check --fix shared services && ruff format shared services; \
	else \
		echo "ERROR: need uv or ruff to format. $(INSTALL_UV) (or: pip install ruff)"; exit 1; \
	fi

benchmark:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run poe benchmark; \
	elif [ -n "$(PY)" ]; then \
		echo "uv not found — running $(PY) dev/scripts/benchmark.py"; \
		$(PY) dev/scripts/benchmark.py; \
	else \
		echo "ERROR: need uv or python3 to run the benchmark. $(INSTALL_UV)"; exit 1; \
	fi

gen-data:
	@if [ -n "$(HAS_UV)" ]; then \
		uv run poe generate-data $(ARGS); \
	elif [ -n "$(PY)" ]; then \
		echo "uv not found — running $(PY) dev/scripts/generate_data.py"; \
		$(PY) dev/scripts/generate_data.py $(ARGS); \
	else \
		echo "ERROR: need uv or python3 to generate data. $(INSTALL_UV)"; exit 1; \
	fi

# Generate fresh synthetic data, then bring the whole stack up.
gen-run: gen-data run

