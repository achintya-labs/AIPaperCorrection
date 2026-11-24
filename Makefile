.PHONY: cert help setup test test-api test-web test-pg test-claude ci-lint up down restart rebuild logs ps

COMPOSE := ./scripts/compose.sh

help:
	@echo "make cert         self-signed TLS cert for the proxy (or ./scripts/make-cert.sh <host-or-ip>...)"
	@echo "make setup        one-time: .venv, web deps, commit hook"
	@echo "make test         the checks the commit hook and CI run (API + web)"
	@echo "make test-claude  live smoke-test against the running claude container"
	@echo "make up           start the stack (podman-compose from .venv)"
	@echo "make test-pg  Postgres-only tests against a throwaway container (podman or docker)"
	@echo "make rebuild      rebuild api/worker/web/claude images and recreate those containers"
	@echo "make down     stop and remove containers (volumes and data are kept)"
	@echo "make logs     follow logs;  make ps  show containers"

cert:
	./scripts/make-cert.sh

setup:
	./scripts/setup-dev.sh

test:
	./scripts/precommit.sh --all

test-api:
	cd api && $(CURDIR)/.venv/bin/python -m pytest

test-web:
	cd web && npm test && npx astro sync && npx tsc --noEmit

test-pg:
	./scripts/test-postgres.sh

test-claude:
	./scripts/test-claude.sh

ci-lint:
	.venv/bin/actionlint

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

rebuild:
	$(COMPOSE) build api worker web claude
	$(COMPOSE) up -d --force-recreate --no-deps api worker web claude

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps
