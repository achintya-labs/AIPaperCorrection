.PHONY: cert help setup test test-api test-web test-pg test-claude ci-lint up down restart pull pullup rebuild logs ps migrate

# Everything that touches containers goes through run.sh (profile for AUTH_MODE, images, certs, LLDAP setup), never a bare podman-compose.
RUN := ./run.sh

help:
	@echo "make cert         self-signed TLS cert for the local-mode proxy (or ./scripts/make-cert.sh <host-or-ip>...)"
	@echo "make setup        one-time: .venv, web deps, commit hook"
	@echo "make test         the checks the commit hook and CI run (API + web)"
	@echo "make test-claude  live smoke-test against the running claude container"
	@echo "make up           ./run.sh up: start (or recreate) the stack for your AUTH_MODE"
	@echo "make test-pg  Postgres-only tests against a throwaway container (podman or docker)"
	@echo "make rebuild      rebuild api/worker/web/claude images and recreate those containers"
	@echo "make down         ./run.sh down: stop and remove containers (volumes and data are kept)"
	@echo "make restart      ./run.sh restart;  make pull / pullup  download / update to the latest images"
	@echo "make migrate      ./run.sh migrate: copy existing users into LLDAP (sso, manual)"
	@echo "make logs         follow logs;  make ps  show containers   (both via ./run.sh)"

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
	$(RUN) up

down:
	$(RUN) down

restart:
	$(RUN) restart

pull:
	$(RUN) pull

pullup:
	$(RUN) pullup

migrate:
	$(RUN) migrate

rebuild:
	$(RUN) compose build api worker web claude
	$(RUN) compose up -d --force-recreate --no-deps api worker web claude

logs:
	$(RUN) logs

ps:
	$(RUN) status
