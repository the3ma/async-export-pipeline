.PHONY: help up seed down logs smoke test test-py test-web test-infra lint

SEED_ROWS ?= 1200000

help:          ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

up:            ## build and start the local stack
	docker compose up -d --build --wait

seed:          ## load SEED_ROWS demo events (default 1.2M)
	SEED_ROWS=$(SEED_ROWS) docker compose --profile seed run --rm seed

smoke:         ## end-to-end: big multi-part export -> zip -> audited download -> e-mail
	./scripts/smoke.sh

logs:          ## follow api + worker logs
	docker compose logs -f api worker

down:          ## stop and remove containers and volumes
	docker compose down -v

test: test-py test-web test-infra   ## run every test suite

test-py:
	uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q

test-web:
	cd web && npm ci && npm run lint && npm run typecheck && npm test -- --run && npm run build

test-infra:
	cd infra && npm ci && npm run lint && npm test && npm run synth -- -q
