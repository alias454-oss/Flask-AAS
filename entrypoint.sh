#!/usr/bin/env bash
set -e

# Enable debug mode if requested
if [[ "${DEBUG}" == "yes" ]]; then
    set -o xtrace
fi

# --- WAIT FOR DATABASE ---
#echo "[+] Waiting for PostgreSQL to be ready on db:5432..."
## This is a simple bash loop that tries to connect to the port
#while ! python -c "import socket; s = socket.socket(); s.connect(('localhost', 5432))" > /dev/null 2>&1; do
#    sleep 1
#done
#echo "[✓] PostgreSQL is up!"

# Paths
_seeded="/base/.seeded"
_init_completed="/base/.db_initialized"

# Optional: wait-for-it db:5432 --timeout=30

# 1. Initialize migrations if needed (only first time)
# 2. Generate migration scripts (diff of models vs DB)
# 3. Apply migrations (create/update tables)
if [ ! -f "${_init_completed}" ]; then
    echo "[+] Initializing database..."
    python manage.py db init || echo "DB already initialized"
    python manage.py db migrate -m "Initial migration: create tables"
    python manage.py db upgrade
    touch "${_init_completed}"
else
    echo "[✓] Database already initialized"
fi

# 4. Run your seed script after migrations
if [ ! -f "${_seeded}" ]; then
    echo "[+] Seeding initial data..."
    python manage.py seed-db
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
