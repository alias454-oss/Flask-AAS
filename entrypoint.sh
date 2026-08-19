#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 ]]; then
    echo "[+] Running custom command: $*"
    exec "$@"
fi

echo "[+] Applying database migrations..."
python manage.py db upgrade

echo "[+] Seeding initial data..."
python manage.py seed-db

echo "[+] Starting Gunicorn..."
exec gunicorn --bind "0.0.0.0:${PORT:-5000}" app.wsgi:app