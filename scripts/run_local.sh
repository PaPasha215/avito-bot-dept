#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "No .env found. Copy .env.example to .env and fill credentials."
  exit 1
fi

python3 -m app.cli validate-env
uvicorn app.main:app --host 0.0.0.0 --port 8000
