#!/bin/bash
# Development: the backend on 8790 with the fixed token "dev", and Vite on 5173 with live reload.
# Ctrl-C stops both. Use only on your own machine: the fixed token is not a secret.
set -euo pipefail
cd "$(dirname "$0")/.."

(cd backend && exec .venv/bin/python -m thor serve --dev --port 8790) &
backend=$!
trap 'kill $backend 2>/dev/null' EXIT

cd ui
npx vite --open "/auth?token=dev"
