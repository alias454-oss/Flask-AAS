#!/usr/bin/env bash
set -euo pipefail

# Use a short hash of the configured DB URI so SQLite and PostgreSQL
# initialization state cannot collide.
_db_key="$(
    printf '%s' "${SQLALCHEMY_DATABASE_URI}" |
    sha256sum |
    cut -c1-16
)"

_seeded="/base/.seeded.${_db_key}"
_init_completed="/base/.db_initialized.${_db_key}"

if [ ! -f "${_init_completed}" ]; then
    echo "[+] Initializing database..."

    python manage.py db init || echo "[i] Migration repository already initialized"
    python manage.py db migrate -m "Initial migration: create tables"
    python manage.py db upgrade

    touch "${_init_completed}"
else
    echo "[✓] Database already initialized"
fi

if [ ! -f "${_seeded}" ]; then
    echo "[+] Seeding initial data..."
    python manage.py seed-db
    touch "${_seeded}"
else
    echo "[✓] Seed already completed"
fi

if [[ $# -eq 0 ]]; then
    echo "[+] Starting Gunicorn..."
    exec gunicorn --bind "0.0.0.0:${PORT:-5000}" app.wsgi:app
else
    echo "[+] Running custom command: $*"
    exec "$@"
fi