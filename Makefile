.PHONY: help init build up down logs migrate health workers search monitoring backup restore test preflight clean deploy-prod rollback verify-backup seed-admin validate-env openapi release-notes diagnostics setup-wizard check-integrations readiness pilot-sheet readiness-gate loadtest performance-budget security-audit media-jobs loadtest-catalog loadtest-webhooks real-e2e grafana-dashboards simple-start launch-local launch-production connected-audit simplicity-score env-todo pilot-runner release-pack release-freeze pilot-evidence package-audit test-all transaction-integrity

help:
	@echo "FLASHIN commands:"
	@echo "  make init                  - first local bootstrap"
	@echo "  make build                 - build containers"
	@echo "  make up                    - start app"
	@echo "  make down                  - stop app"
	@echo "  make migrate               - run Alembic migrations"
	@echo "  make transaction-integrity - read-only database integrity audit"
	@echo "  make health                - check health"
	@echo "  make workers               - run worker profile"
	@echo "  make search                - start Meilisearch"
	@echo "  make monitoring            - start Prometheus/Grafana"
	@echo "  make test                  - run backend tests"
	@echo "  make backup                - backup PostgreSQL"

init:
	./scripts/bootstrap.sh

build:
	docker compose build

up:
	docker compose up -d backend frontend admin bot

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	./scripts/migrate.sh

transaction-integrity:
	docker compose run --rm backend python scripts/check_transaction_integrity.py

health:
	./scripts/healthcheck.sh

workers:
	docker compose --profile workers up -d

search:
	docker compose --profile search up -d meilisearch
	docker compose run --rm backend python scripts/configure_meilisearch.py

monitoring:
	docker compose --profile monitoring up -d prometheus grafana

backup:
	./scripts/backup_postgres.sh

restore:
	@echo "Usage: make restore FILE=backups/flashin_xxx.sql.gz"
	./scripts/restore_postgres.sh $(FILE)

test:
	docker compose run --rm backend pytest backend/tests

preflight:
	python3 scripts/preflight.py

clean:
	docker compose down -v


deploy-prod:
	./scripts/deploy_production.sh

rollback:
	@echo "Usage: make rollback RELEASE=previous.zip BACKUP=backup.sql.gz"
	./scripts/rollback.sh $(RELEASE) $(BACKUP)

verify-backup:
	@echo "Usage: make verify-backup FILE=backups/flashin_xxx.sql.gz"
	./scripts/verify_backup.sh $(FILE)

seed-admin:
	docker compose run --rm backend python scripts/seed_admin.py


validate-env:
	python3 scripts/validate_env.py

openapi:
	docker compose run --rm backend python scripts/generate_openapi_snapshot.py

release-notes:
	python3 scripts/generate_release_notes.py

diagnostics:
	@echo "Open admin-authenticated endpoint: /api/diagnostics"


setup-wizard:
	python3 scripts/setup_wizard.py

check-integrations:
	python3 scripts/check_integrations.py

readiness:
	python3 scripts/production_readiness_report.py

pilot-sheet:
	python3 scripts/generate_20_order_pilot_sheet.py


readiness-gate:
	python3 scripts/readiness_gate.py


loadtest:
	API_BASE=http://localhost:8000 k6 run deploy/loadtest/k6_smoke.js

performance-budget:
	python3 scripts/performance_budget.py


security-audit:
	./scripts/security_audit.sh

media-jobs:
	docker compose --profile workers run --rm media_jobs

loadtest-catalog:
	API_BASE=http://localhost:8000 k6 run deploy/loadtest/k6_catalog_search_checkout.js

loadtest-webhooks:
	API_BASE=http://localhost:8000 k6 run deploy/loadtest/k6_webhook_burst.js


real-e2e:
	RUN_REAL_E2E=1 pytest backend/tests/e2e

grafana-dashboards:
	@ls -1 deploy/grafana/dashboards


simple-start:
	./scripts/start_simple.sh

launch-local:
	python3 scripts/launch.py --mode local --with-search --with-workers

launch-production:
	python3 scripts/launch.py --mode production --with-search --with-workers --with-monitoring

connected-audit:
	python3 scripts/connected_system_audit.py

simplicity-score:
	python3 scripts/simplicity_score.py


env-todo:
	python3 scripts/generate_env_todo.py

pilot-runner:
	python3 scripts/pilot_runner.py

release-pack:
	python3 scripts/generate_release_pack.py


release-freeze:
	python3 scripts/release_freeze.py

pilot-evidence:
	python3 scripts/pilot_evidence_log.py


package-audit:
	python3 scripts/package_audit.py

test-all:
	./scripts/test_all.sh
