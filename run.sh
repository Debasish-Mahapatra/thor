#!/bin/bash
# Builds whatever is out of date and opens Thor.
#   ./run.sh            the app
#   ./run.sh --browser  the same interface in your default browser
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v npm >/dev/null; then
  echo "Thor needs Node to build its interface: brew install node" >&2
  exit 1
fi

if [[ "${1:-}" == "--browser" ]]; then
  exec make --no-print-directory browser
fi
make --no-print-directory run
