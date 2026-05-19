.PHONY: install scrape test test-live lint fmt clean dev-api dev-ui eval eval-mock

PY ?= python
VENV ?= .venv
BIN := $(VENV)/bin

TOPIC ?= WhatsApp
LIMIT ?= 100
SINCE ?= 365d
REGION ?= HK
SOURCES ?= app_store_hk

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

scrape:
	$(BIN)/mkt scrape --topic "$(TOPIC)" --region $(REGION) --sources $(SOURCES) --limit $(LIMIT) --since $(SINCE)

test:
	$(BIN)/pytest -q

test-live:
	SCRAPE_LIVE_TESTS=1 $(BIN)/pytest -q tests/integration

lint:
	$(BIN)/ruff check src tests

fmt:
	$(BIN)/ruff format src tests

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__

dev-api:
	$(BIN)/uvicorn src.api.app:app --reload --host 127.0.0.1 --port 8000

dev-ui:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

ui-install:
	cd ui && npm install

# Eval suite — 5 frozen product fixtures with ground-truth pain points.
# `make eval-mock` replays canned LLM responses (no API key) — for CI.
# `make eval`      drives the real LLM (set ANTHROPIC_API_KEY first) —
#                  for measuring the impact of a prompt change.
eval-mock:
	$(BIN)/mkt eval --provider mock --min-recovery 0.6

eval:
	$(BIN)/mkt eval --provider anthropic --min-recovery 0.5
