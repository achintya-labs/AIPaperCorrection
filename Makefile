.PHONY: help setup test test-api test-web test-pg ci-lint up down restart rebuild logs ps

COMPOSE := ./scripts/compose.sh

help:
	@echo "make setup    one-time: .venv, web deps, commit hook"
	@echo "make test     the checks the commit hook and CI run (API + web)"
	@echo "make up       start the stack (podman-compose from .venv)"
	@echo "make test-pg  Postgres-only tests against a throwaway container (podman or docker)"
	@echo "make rebuild  rebuild api/worker/web images and recreate those containers"
	@echo "make down     stop and remove containers (volumes and data are kept)"
	@echo "make logs     follow logs;  make ps  show containers"

setup:
	./scripts/setup-dev.sh

test:
	./scripts/precommit.sh --all

test-api:
	cd api && $(CURDIR)/.venv/bin/python -m pytest

test-web:
	cd web && npm test && npx tsc --noEmit

test-pg:
	./scripts/test-postgres.sh

ci-lint:
	.venv/bin/actionlint

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

rebuild:
	$(COMPOSE) build api worker web
	$(COMPOSE) up -d --force-recreate --no-deps api worker web

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps
