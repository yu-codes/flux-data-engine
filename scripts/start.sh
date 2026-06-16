#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
docker compose up --build -d backend frontend
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3001"
