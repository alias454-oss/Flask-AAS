# manage.py
"""
Flask-AAS Management CLI

# Verify the pathing
python3 manage.py --help

# Verify DB connection (Peer Auth should work locally if configured)
python3 manage.py db current

# Development INIT
  python manage.py db init                  # Initialize migrations/ folder
  python manage.py db migrate -m "msg"      # Generate new migration version
  python manage.py db upgrade               # Build/Sync schema via Peer Auth

# Purge AuditLogin records (default 7 days)
python manage.py cleanup-logins --days=30 # Purge with custom retention

# Run data seeder on fresh DB
python manage.py seed-db
"""

import click
import logging
from flask.cli import FlaskGroup
from flask_migrate import Migrate
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete

from app import create_app, db
from app.core.seeder import run_all_seeds
from app.models.audit_login import AuditLogin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_cli_app():
    return create_app()

# Initialize Migrate for the 'flask db' commands
# We instantiate the app once here for the migration context
app = create_app()
migrate = Migrate(app, db)

# Create CLI group
cli = FlaskGroup(create_app=create_cli_app)

@cli.command("seed-db")
def seed_db():
    """
    Hardened idempotency check and initial data on Fresh instance.
    """
    try:
        # We pass 'app' so the seeder can access app.config for secrets
        with app.app_context():
            run_all_seeds()
        logger.info("Database seeding successful.")
    except Exception as e:
        logger.error(f"Seeding failed: {str(e)}")
        exit(1)

@cli.command("cleanup-logins")
@click.option('--days', default=7, help="Delete entries older than X number of days.")
def cleanup_logins(days):
    """
    Delete old login attempts from AuditLogin table.

    Usage:
      python manage.py cleanup-logins
      python manage.py cleanup-logins --days=30
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        # Use a direct delete query for performance
        num_deleted = db.session.query(AuditLogin).filter(AuditLogin.timestamp < cutoff).delete()

        db.session.commit()
        logger.info(f"Successfully deleted {num_deleted} login records older than {days} days.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to cleanup login logs: {str(e)}")
        exit(1)

if __name__ == '__main__':
    cli()
