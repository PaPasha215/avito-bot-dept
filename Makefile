.PHONY: run poll-once validate-env test compile docker-up

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

poll-once:
	python3 -m app.cli poll-once

validate-env:
	python3 -m app.cli validate-env

test:
	pytest -q

compile:
	python3 -m compileall app

docker-up:
	docker compose up --build
