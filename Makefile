.PHONY: run poll-once learn-once learning-status stats-report-once validate-env validate-youla-readiness chat-diagnostics migrate-sqlite-to-postgres test compile docker-up

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

poll-once:
	python3 -m app.cli poll-once

learn-once:
	python3 -m app.cli learn-once

learning-status:
	python3 -m app.cli learning-status

stats-report-once:
	python3 -m app.cli stats-report-once

validate-env:
	python3 -m app.cli validate-env

validate-youla-readiness:
	python3 -m app.cli validate-youla-readiness

chat-diagnostics:
	@if [ -z "$(CHAT_ID)" ]; then echo "Usage: make chat-diagnostics CHAT_ID=<external_chat_id>"; exit 2; fi
	python3 -m app.cli chat-diagnostics --external-chat-id "$(CHAT_ID)"

migrate-sqlite-to-postgres:
	@if [ -z "$(DST)" ]; then echo "Usage: make migrate-sqlite-to-postgres DST=<postgresql_url> [SRC=sqlite:///./data/app.db]"; exit 2; fi
	python3 scripts/migrate_sqlite_to_postgres.py --src "$(or $(SRC),sqlite:///./data/app.db)" --dst "$(DST)" --replace

test:
	pytest -q

compile:
	python3 -m compileall app

docker-up:
	docker compose up --build
