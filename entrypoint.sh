#!/usr/bin/env bash
set -e

# Enable debug mode if requested
if [[ "${DEBUG}" == "yes" ]]; then
    set -o xtrace
fi

# Paths
_seeded="/base/.seeded"
_init_completed="/base/.db_initialized"

# Optional: wait-for-it db:5432 --timeout=30

# 1. Initialize migrations if needed (only first time)
# 2. Generate migration scripts (diff of models vs DB)
# 3. Apply migrations (create/update tables)
if [ ! -f "${_init_completed}" ]; then
    echo "[+] Initializing database..."
    python -m app.manage db init || echo "DB already initialized"
    python -m app.manage db migrate -m "Initial migration: create tables"
    python -m app.manage db upgrade
    touch "${_init_completed}"
else
    echo "[✓] Database already initialized"
fi

# 4. Run your seed script after migrations
if [ ! -f "${_seeded}" ]; then
    echo "[+] Seeding initial data..."
    python -m app.seed_data
    touch "${_seeded}"
else
    echo "[✓] Seed already completed"
fi

# If no command-line arguments are passed, run Gunicorn
# run a one off docker run yourapp flask db upgrade
if [[ $# -eq 0 ]]; then
    # Normal docker run yourapp
    echo "[+] Starting Gunicorn..."
    exec gunicorn --bind 0.0.0.0:5000 app.wsgi:app
else
    # allow docker run -it yourapp /bin/bash
    echo "[+] Running custom command: \$@"
    exec "$@"
fi
