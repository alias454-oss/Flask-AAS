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

# Test outbound email using Admin Email or an explicit recipient
python manage.py mail-test
python manage.py mail-test --to operator@example.com
"""

import click
import logging

from email_validator import EmailNotValidError, validate_email
from flask.cli import FlaskGroup
from flask_migrate import Migrate
from datetime import datetime, timedelta, timezone

from app import create_app, db
from app.core.mailer import get_mail_env_settings, send_email
from app.core.migrations import (
    core_migration_include_name,
    core_migration_include_object,
)
from app.core.seeder import run_all_seeds
from app.models.audit_login import AuditLogin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_cli_app():
    return create_app()


# Initialize Migrate for the 'flask db' commands
# We instantiate the app once here for the migration context
app = create_app()
migrate = Migrate(
    app,
    db,
    include_name=core_migration_include_name,
    include_object=core_migration_include_object,
)

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


@cli.command("mail-test")
@click.option("--to", "recipient", metavar="EMAIL", help="Send to EMAIL instead of current Admin Email.")
def mail_test(recipient):
    """Queue a diagnostic email through the active mail configuration."""
    try:
        with app.app_context():
            if recipient is None:
                env = get_mail_env_settings()
                recipient = getattr(env, "admin_email", None) if env else None

                if not recipient:
                    logger.error("Mail test failed: Admin Email is not configured; use --to EMAIL.")
                    exit(1)

            try:
                recipient = validate_email(recipient, check_deliverability=False).normalized
            except EmailNotValidError:
                logger.error("Mail test failed: Recipient email address is invalid.")
                exit(1)

            status = send_email(
                subject="Flask-AAS outbound email test",
                recipient=recipient,
                text_body=(
                    "Flask-AAS accepted this test email for delivery through "
                    "the active outbound email configuration."
                ),
                html_body=(
                    "<p>Flask-AAS accepted this test email for delivery through "
                    "the active outbound email configuration.</p>"
                ),
            )

        if status == "queued":
            logger.info(f"Test email queued for {recipient}.")
            return

        if status == "disabled":
            logger.error("Mail test failed: Outbound email is disabled or unavailable.")
        else:
            logger.error("Mail test failed: The test email could not be queued.")

        exit(1)

    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Mail test failed: {str(e)}")
        exit(1)


if __name__ == '__main__':
    cli()
