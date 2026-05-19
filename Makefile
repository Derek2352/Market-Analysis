.PHONY: install scrape test test-live test-render lint fmt clean dev-api dev-ui eval eval-mock render render-zip

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

# Phase 8 — render personas + journeys to shareable PNGs.
#
#   make render RUN_ID=20260519T... — render bundle for one run
#   make render-zip RUN_ID=...      — same, plus a {run_id}.zip
#   make test-render                — Playwright-driven render tests
RUN_ID ?= $(error set RUN_ID=YYYYMMDDTHHMMSSZ)

render:
	$(BIN)/mkt render run $(RUN_ID)

render-zip:
	$(BIN)/mkt render run $(RUN_ID) --zip

test-render:
	$(BIN)/pytest -q tests/render/
