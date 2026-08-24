#!/usr/bin/env bash
# Stop the stack. Pass --clean to drop volumes (database, storage) as well.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--clean" ]; then
  docker compose down -v
  echo "stack stopped and volumes removed"
else
  docker compose down
  echo "stack stopped (volumes kept)"
fi
