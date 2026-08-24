#!/usr/bin/env bash
# Bring the whole stack up and wait until the API answers.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi

docker compose up -d --build

printf 'waiting for the API'
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:38000/health >/dev/null 2>&1; then
    echo
    echo "frontend : http://localhost:3001"
    echo "api      : http://localhost:38000"
    echo "api docs : http://localhost:38000/docs"
    echo "minio    : http://localhost:39001"
    exit 0
  fi
  printf '.'
  sleep 2
done

echo
echo "the API did not become healthy in time; check: docker compose logs backend" >&2
exit 1
