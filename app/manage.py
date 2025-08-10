# manage.py
"""
Usage:
  python manage.py db init                  # Initialize migrations folder (only once)
  python manage.py db migrate -m "msg"     # Auto-generate migration scripts
  python manage.py db upgrade              # Apply changes to DB
  python manage.py db downgrade            # (optional) Roll back last migration

  python manage.py cleanup-logins          # Delete AuditLogin records older than 7 days (default)
  python manage.py cleanup-logins --days=30  # Use custom age cutoff (in days)
"""
import click
from flask.cli import FlaskGroup
from flask_migrate import Migrate
from datetime import datetime, timedelta, timezone

from app import create_app, db
from app.models.audit_login import AuditLogin

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create app using factory
app = create_app()

# Set up migration context
migrate = Migrate(app, db)

# Create CLI group
cli = FlaskGroup(create_app=create_app)

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
    old_entries = AuditLogin.query.filter(AuditLogin.timestamp < cutoff).all()
    deleted_count = len(old_entries)

    for entry in old_entries:
        db.session.delete(entry)

    db.session.commit()
    msg = f"Deleted {deleted_count} login attempt(s) older than {days} day(s)."
    logger.info(msg)

if __name__ == '__main__':
    cli()
