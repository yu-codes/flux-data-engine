#!/usr/bin/env bash
# Run every check the project has.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== backend: ruff =="
(cd backend && python -m ruff check app tests)

echo "== backend: pytest =="
(cd backend && python -m pytest -q)

echo "== frontend: typecheck + build =="
(cd frontend && npm run build)

#  Both of these need something that may not be there - a running backend, a
#  local Chrome - and both say so and pass rather than pretending to check.
echo "== frontend: API type drift =="
(cd frontend && npm run types:check)

#  Before layout, because "the page is broken" matters more than "the page is
#  slightly too wide", and this is the check that would have caught a database
#  a migration behind the code - which every other check here misses.
echo "== frontend: pages =="
(cd frontend && npm run check:pages)

echo "== frontend: layout =="
(cd frontend && npm run check:layout)

echo "all checks passed"
