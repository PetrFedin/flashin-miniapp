.PHONY: help init build up down logs migrate health workers search monitoring backup restore test preflight clean deploy-prod rollback rollback-drill rollback-drill-status verify-backup seed-admin validate-env openapi release-notes diagnostics setup-wizard check-integrations provider-probes readiness pilot-sheet readiness-gate pilot-gate loadtest performance-budget security-audit media-jobs loadtest-catalog loadtest-webhooks real-e2e grafana-dashboards simple-start launch-local launch-production connected-audit simplicity-score env-todo pilot-runner pilot-init pilot-record pilot-status pilot-final pilot-admit pilot-admission-status pilot-lifecycle-create pilot-lifecycle-attach pilot-lifecycle-status pilot-governance-create pilot-governance-attach pilot-governance-status pilot-runtime-arm pilot-runtime-status pilot-runtime-stop release-create release-verify release-status release-pack release-freeze pilot-evidence package-audit test-all transaction-integrity

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
	@echo "  make readiness-gate        - strict predeploy GO/NO-GO gate"
	@echo "  make provider-probes       - run side-effectful live provider probes once"
	@echo "  make pilot-gate            - verify public endpoints and signed provider evidence"
	@echo "  make rollback-drill        - execute rollback and record signed drill evidence"
	@echo "  make pilot-admit           - create signed human/business pilot admission"
	@echo "  make pilot-lifecycle-create - sign file-backed deployed lifecycle evidence"
	@echo "  make pilot-lifecycle-attach - bind lifecycle evidence to pilot admission"
	@echo "  make pilot-lifecycle-status - verify admission plus lifecycle attachment"
	@echo "  make pilot-governance-create - sign exact GitHub branch/rules/CI evidence"
	@echo "  make pilot-governance-attach - bind GitHub governance evidence to admission"
	@echo "  make pilot-governance-status - verify admission, lifecycle and governance"
	@echo "  make pilot-runner          - initialize/show admission-gated 20-order control"
	@echo "  make pilot-runtime-arm     - open checkout for allowlisted pilot Telegram IDs"
	@echo "  make pilot-runtime-status  - verify DB counter, evidence binding and remaining slots"
	@echo "  make pilot-runtime-stop    - immediately block new pilot checkout"
	@echo "  make pilot-status          - recalculate current pilot decision"
	@echo "  make pilot-final           - require all 20 pilot scenarios and final GO"
	@echo "  make release-create        - build immutable tracked-files release ZIP"
	@echo "  make release-status        - show current/previous local release pointers"
	@echo "  make backup                - backup PostgreSQL"
	@echo "  make rollback              - restore previous verified release and database backup"

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
	bash ./scripts/backup_postgres.sh

restore:
	@echo "Usage: make restore FILE=backups/flashin_xxx.sql.gz"
	bash ./scripts/restore_postgres.sh $(FILE)

test:
	docker compose run --rm backend pytest backend/tests

preflight:
	python3 scripts/preflight.py

clean:
	docker compose down -v


deploy-prod:
	bash ./scripts/deploy_production.sh

rollback:
	@echo "Usage: make rollback RELEASE=previous BACKUP=backups/flashin_xxx.sql.gz"
	bash ./scripts/rollback.sh $(if $(RELEASE),$(RELEASE),previous) $(BACKUP)

rollback-drill:
	@echo "Usage: make rollback-drill RELEASE=previous BACKUP=backups/flashin_xxx.sql.gz"
	ROLLBACK_DRILL=1 bash ./scripts/rollback.sh $(if $(RELEASE),$(RELEASE),previous) $(BACKUP)

rollback-drill-status:
	python3 scripts/pilot_evidence.py verify-rollback

verify-backup:
	@echo "Usage: make verify-backup FILE=backups/flashin_xxx.sql.gz"
	bash ./scripts/verify_backup.sh $(FILE)

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
	python3 scripts/check_integrations.py verify

provider-probes:
	@echo "Creates one idempotent 1.00 RUB YooKassa pending payment for the current release."
	python3 scripts/check_integrations.py run --acknowledge-side-effects $(ARGS)

readiness:
	python3 scripts/production_readiness_report.py

pilot-sheet:
	python3 scripts/generate_20_order_pilot_sheet.py


readiness-gate:
	python3 scripts/readiness_gate.py --phase predeploy

pilot-gate:
	python3 scripts/readiness_gate.py --phase live


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

pilot-admit:
	@echo "Usage: make pilot-admit ARGS='--business-owner ... --operations-owner ... --technical-owner ... --legal-owner ... --support-owner ... --legal-documents-approved --support-process-ready --rollback-drill-completed --provider-probe-side-effect-understood --pilot-scope-limited-to-20-orders'"
	python3 scripts/pilot_admission.py create $(ARGS)

pilot-admission-status:
	python3 scripts/pilot_governance_admission.py verify $(ARGS)

pilot-lifecycle-create:
	@echo "Usage: make pilot-lifecycle-create ARGS='--input docs/pilot/live_lifecycle_input.json'"
	python3 scripts/pilot_live_lifecycle.py create $(ARGS)

pilot-lifecycle-attach:
	python3 scripts/pilot_lifecycle_admission.py attach $(ARGS)

pilot-lifecycle-status:
	python3 scripts/pilot_lifecycle_admission.py verify $(ARGS)

pilot-governance-create:
	@echo "Usage: inject PILOT_GITHUB_TOKEN only into this process, then run ARGS='--owner \"Exact technical owner name\"'"
	python3 scripts/pilot_governance_operator.py create $(ARGS)

pilot-governance-attach:
	python3 scripts/pilot_governance_admission.py attach $(ARGS)

pilot-governance-status:
	python3 scripts/pilot_governance_admission.py verify $(ARGS)

pilot-runner:
	python3 scripts/pilot_runner.py

pilot-init:
	@echo "Usage: make pilot-init ARGS='--operator-role operations_owner --operator \"Name\" --reason \"Initialize controlled pilot\" [--force]'"
	python3 scripts/pilot_runner.py init $(ARGS)

pilot-runtime-arm:
	@echo "Usage: make pilot-runtime-arm ARGS='--telegram-id 123456789 [--telegram-id ...] [--resume]'"
	python3 scripts/pilot_runtime.py arm $(ARGS)

pilot-runtime-status:
	python3 scripts/pilot_runtime.py status

pilot-runtime-stop:
	@test -n "$(REASON)" || (echo "Usage: make pilot-runtime-stop REASON='operator stop reason'"; exit 1)
	python3 scripts/pilot_runtime.py stop --reason "$(REASON)"

pilot-record:
	@echo "Usage: make pilot-record ARGS='--number 1 --result pass --operator-role operations_owner --operator \"Name\" --reason \"Verified scenario\" ...'"
	python3 scripts/pilot_runner.py record $(ARGS)

pilot-status:
	python3 scripts/pilot_runner.py status

pilot-final:
	python3 scripts/pilot_runner.py validate --final

release-create:
	python3 scripts/release_control.py create --print-path

release-verify:
	@echo "Usage: make release-verify FILE=deploy/release/builds/flashin_xxx.zip"
	python3 scripts/release_control.py verify --archive $(FILE)

release-status:
	python3 scripts/release_control.py status

release-pack: release-create

release-freeze: release-create

pilot-evidence:
	python3 scripts/pilot_evidence_log.py


package-audit:
	python3 scripts/package_audit.py

test-all:
	./scripts/test_all.sh
