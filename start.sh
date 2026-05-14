#!/bin/bash
# Framebench - Start both backend and frontend

set -euo pipefail

echo "Starting Framebench..."

cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
command -v npx >/dev/null || { echo "npx is required"; exit 1; }

BACKEND_PORT="${FRAMEBENCH_BACKEND_PORT:-${FILM_MASTER_BACKEND_PORT:-8000}}"
FRONTEND_PORT="${FRAMEBENCH_FRONTEND_PORT:-${FILM_MASTER_FRONTEND_PORT:-5174}}"
DEFAULT_DATA_ROOT="$HOME/Library/Application Support/拉片工作台"
FRAMEBENCH_DATA_ROOT="$HOME/Library/Application Support/Framebench"
LEGACY_DATA_ROOT="$HOME/Library/Application Support/film-master"
if [ -n "${FRAMEBENCH_DATA_DIR:-}" ]; then
  DATA_ROOT="$FRAMEBENCH_DATA_DIR"
elif [ -n "${FILM_MASTER_DATA_DIR:-}" ]; then
  DATA_ROOT="$FILM_MASTER_DATA_DIR"
elif [ ! -f "$DEFAULT_DATA_ROOT/film_master.db" ] && [ -f "$LEGACY_DATA_ROOT/film_master.db" ]; then
  DATA_ROOT="$LEGACY_DATA_ROOT"
elif [ ! -f "$FRAMEBENCH_DATA_ROOT/film_master.db" ] && [ -f "$DEFAULT_DATA_ROOT/film_master.db" ]; then
  DATA_ROOT="$DEFAULT_DATA_ROOT"
else
  DATA_ROOT="$FRAMEBENCH_DATA_ROOT"
fi
LOCAL_TOKEN="${FRAMEBENCH_LOCAL_TOKEN:-${FILM_MASTER_LOCAL_TOKEN:-$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)}}"

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

if port_in_use "$BACKEND_PORT"; then
  echo "Backend port $BACKEND_PORT is already in use."
  exit 1
fi

if port_in_use "$FRONTEND_PORT"; then
  echo "Frontend port $FRONTEND_PORT is already in use."
  exit 1
fi

cleanup() {
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}

trap "cleanup; exit" INT TERM EXIT

# Start backend
FRAMEBENCH_DATA_DIR="$DATA_ROOT" \
FRAMEBENCH_LOCAL_TOKEN="$LOCAL_TOKEN" \
FILM_MASTER_DATA_DIR="$DATA_ROOT" \
FILM_MASTER_LOCAL_TOKEN="$LOCAL_TOKEN" \
PYTHONPATH=. python3 -m uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"

# Start frontend
cd frontend
VITE_API_URL="http://127.0.0.1:$BACKEND_PORT/api" \
VITE_API_TOKEN="$LOCAL_TOKEN" \
npx vite --host 127.0.0.1 --port "$FRONTEND_PORT" &
FRONTEND_PID=$!
echo "Frontend starting... (PID: $FRONTEND_PID)"

echo ""
echo "Framebench is running:"
echo "  Frontend: http://127.0.0.1:$FRONTEND_PORT"
echo "  Backend:  http://127.0.0.1:$BACKEND_PORT"
echo "  Data:     $DATA_ROOT"
echo ""
echo "Press Ctrl+C to stop both services."

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend stopped unexpectedly."
    exit 1
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "Frontend stopped unexpectedly."
    exit 1
  fi
  sleep 1
done
