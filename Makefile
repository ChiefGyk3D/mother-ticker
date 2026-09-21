# Mother Ticker developer targets. `make check` reproduces CI minus the matrix.
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: venv check lint type test security yaml ansible shell hygiene format clean screenshots grafana

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

check: lint type test security yaml ansible shell hygiene

lint:
	$(BIN)/ruff check src tests scripts
	$(BIN)/ruff format --check src tests scripts

format:
	$(BIN)/ruff format src tests scripts
	$(BIN)/ruff check --fix src tests scripts

type:
	$(BIN)/mypy

test:
	$(BIN)/pytest

security:
	$(BIN)/bandit -c pyproject.toml -r src -q

yaml:
	$(BIN)/yamllint -c .yamllint.yml .

ansible:
	cd ansible && ../$(BIN)/ansible-lint

shell:
	shellcheck -x scripts/*.sh ansible/roles/mother_ticker/files/*.sh

hygiene:
	$(BIN)/pytest tests/test_repo_hygiene.py -q

clean:
	rm -rf build dist *.egg-info .mypy_cache .ruff_cache .pytest_cache

# Screenshots for the README come from the demo app, never by hand.
screenshots:
	$(BIN)/python scripts/render_screenshots.py docs/images

# The Grafana dashboard JSON is generated; edit the generator, then run this.
grafana:
	$(BIN)/python scripts/gen_grafana_dashboard.py
