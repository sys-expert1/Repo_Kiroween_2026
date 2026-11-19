#!/usr/bin/env bash
# Build the dashboard and start the API and UI together on http://127.0.0.1:8000.
# Requires Python 3.12+ and Node 20+. Set NECRO_ALLOWED_ROOTS to allow local paths.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -x "$root/backend/.venv/bin/python" ]; then
  python3.12 -m venv "$root/backend/.venv"
  "$root/backend/.venv/bin/pip" install -r "$root/backend/requirements-dev.txt"
fi
if [ ! -d "$root/frontend/node_modules" ]; then
  (cd "$root/frontend" && npm ci)
fi
(cd "$root/frontend" && npm run build)

cd "$root/backend"
exec .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${PORT:-8000}"
