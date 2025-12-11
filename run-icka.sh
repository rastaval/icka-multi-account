#!/bin/bash
# File: /home/user/icka/run-icka.sh

BASE_DIR="/home/user/icka"
PIDFILE="$BASE_DIR/.icka.pid"
LOGFILE="$BASE_DIR/icka.log"
ENVFILE="$BASE_DIR/.env"
VENVDIR="$BASE_DIR/.venv"
ACTIVATE="$VENVDIR/bin/activate"
PYBIN="$VENVDIR/bin/python"
SCRIPT="$BASE_DIR/icka.py"

cd "$BASE_DIR" || {
  echo "ERROR: cannot cd to $BASE_DIR"
  exit 1
}

# Stop old one if alive
if [ -f "$PIDFILE" ]; then
  OLD_PID=$(cat "$PIDFILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Killing old icka process: $OLD_PID"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
fi

# Clear old log
: > "$LOGFILE"

# Decide which python to use: venv if present, else system python3
if [ -f "$ACTIVATE" ]; then
  echo "Using venv at $VENVDIR"
  # shellcheck disable=SC1090
  . "$ACTIVATE"
  PYTHON_CMD="$PYBIN"
else
  echo "No venv found at $VENVDIR, using system python3"
  PYTHON_CMD="$(command -v python3 || echo python3)"
fi

# Optional: export variables from .env (not strictly required,
# because icka.py already reads .env, but it doesn't hurt)
if [ -f "$ENVFILE" ]; then
  set -a
  . "$ENVFILE"
  set +a
fi

# Start icka
nohup "$PYTHON_CMD" "$SCRIPT" >> "$LOGFILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"

echo "Started icka (PID: $NEW_PID), log: $LOGFILE"
sleep 1
tail -n 30 "$LOGFILE" || true
