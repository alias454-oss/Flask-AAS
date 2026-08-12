# app/core/seeder.py
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select

from flask import current_app
from app import create_app, db
from app.models.country import Country
from app.models.zone import Zone
from app.models.env_settings import EnvSettings
from app.models.user import User
from app.models.role import Role
from app.models.plugin import PluginRegistration
from app.plugins.bundled import bundled_plugin_registrations
from app.core.mailer import environment_mail_configuration

logger = logging.getLogger(__name__)


def initial_outbound_email_enabled() -> bool:
    """Derive the clean-install outbound-email switch from deployment config."""
    return bool(
        current_app.config.get("MAIL_DEBUG", False)
        or environment_mail_configuration() is not None
    )


def seed_roles():
    roles = [
        {"name": "admin", "description": "Administrator with full access"},
        {"name": "editor", "description": "Can edit content"},
        {"name": "moderator", "description": "Can moderate user content"},
        {"name": "user", "description": "Regular user with limited permissions"},
        {"name": "guest", "description": "Guest user with minimal access"},
    ]
    for role_data in roles:
        if not db.session.scalar(select(Role).filter_by(name=role_data["name"])):
            db.session.add(Role(**role_data))
    db.session.commit()
    logger.info("Roles verified/seeded.")


def seed_admin_user():
    """
    Seeds the admin user and its role if they don't exist.
    Relies on app.config for secrets and db_session for persistence.
    """
    logger.info("Attempting to seed Admin User and Role...")

    # Ensure Admin Role Exists
    admin_role = db.session.scalar(select(Role).filter_by(name='admin'))
    if not admin_role:
        logger.info("Admin role not found. Creating admin role.")
        admin_role = Role(name='admin', description='Administrator with full access')
        db.session.add(admin_role)
        db.session.commit()
        db.session.refresh(admin_role)
    else:
        logger.info("Admin role already exists.")

    # Check if Admin User Already Exists
    admin_user = db.session.scalar(select(User).filter_by(username='admin'))
    if admin_user:
        logger.info("Admin user 'admin' already exists. Skipping creation.")
        return

    logger.info("Admin user 'admin' not found. Creating admin user...")

    # Retrieve secrets and config from the Flask app context
    admin_secret = current_app.config.get("ADMIN_SECRET")
    if not admin_secret:
        logger.error("ADMIN_SECRET is not set in Flask config. Cannot create admin user.")
        return

    # Prepare user data with defaults and admin-specific values
    user_data = {
        "username": "admin",
        "email": current_app.config.get("ADMIN_EMAIL", "admin@yoursite.com"),  # Use config or fallback
        "ip_address": "127.0.0.1",  # Default for seeding
        "company_name": "Alias 454 Studio",
        "first_name": "System",
        "last_name": "Administrator",
        "phone": "+01 000-000-0000 x12345",
        "country_code": "US",
        "address": "123 Somewhere",
        "city": "Some City",
        "zone_code": "US-IL",
        "postal_code": "61032",
        "reg_date": datetime.now(timezone.utc),  # Use current time for registration
        "last_active": datetime.now(timezone.utc),  # Set initially
        "activated": True,
        "approved": True,
        "otp_secret": None,
        "mfa_enabled": False
    }

    # Create the User instance
    user = User(**user_data)
    user.set_password(admin_secret)

    # Assign the role
    user.roles.append(admin_role)

    # Save to DB
    db.session.add(user)
    db.session.commit()
    logger.info("Admin user 'admin' created successfully.")


def seed_env_settings():
    """
    Seeds the initial EnvSettings for the admin user using hardcoded defaults.
    Assumes admin user and user role are already created.
    """
    # Ensure Admin User Exists
    admin_user = db.session.scalar(select(User).filter_by(username='admin'))
    if not admin_user:
        logger.error("Cannot seed EnvSettings: Admin user 'admin' not found. Please ensure user seeding runs first.")
        return

    # Ensure User Role Exists
    user_role = db.session.scalar(select(Role).filter_by(name='user'))
    if not user_role:
        logger.warning("User role 'user' not found. Default role ID will be None.")

    # Check if EnvSettings already exists for the admin user
    existing_settings = db.session.scalar(select(EnvSettings).filter_by(user_id=admin_user.id))
    if existing_settings:
        logger.info(f"EnvSettings already exist for user_id {admin_user.id}. Skipping seeding.")
        return

    logger.info(f"Seeding EnvSettings for admin user (ID: {admin_user.id})...")

    # Create EnvSettings record with hardcoded defaults
    settings_data = {
        "user_id": admin_user.id,
        "site_name": "Login Site",
        "site_url": current_app.config.get("SITE_URL", "http://127.0.0.1:5000"),
        "site_lang": "en",
        "site_timezone": "America/Chicago",
        "description": "Short description of site",
        "keywords": "Keyword, keyword one, separate, by commas",
        "admin_name": "admin",
        "admin_email": "admin@site.com",
        "site_mode": 1,  # 0 = public/multi-user, 1 = single-user
        "default_role_id": user_role.id if user_role else None,
        "users_per_page": 10,
        "users_stored_path": "static/images/users",
        "max_failed_attempts": 5,
        "lockout_duration_seconds": 900,
        "password_policy_enabled": current_app.config.get("PASSWORD_POLICY_ENABLED", True),
        "password_min_length": current_app.config.get("PASSWORD_MIN_LENGTH", 20),
        "password_require_uppercase": current_app.config.get("PASSWORD_REQUIRE_UPPERCASE", False),
        "password_require_lowercase": current_app.config.get("PASSWORD_REQUIRE_LOWERCASE", False),
        "password_require_number": current_app.config.get("PASSWORD_REQUIRE_NUMBER", False),
        "password_require_special": current_app.config.get("PASSWORD_REQUIRE_SPECIAL", False),
        "password_check_enabled": False,
        "password_check_provider": "local",
        "template": "default",
        "use_verify_email": 0,
        "use_user_approval": 0,
        "use_user_location": 0,
        "use_captcha": 0,
        "contact_enabled": False,
        "spam_check_enabled": True,
        "spam_check_provider": "local",
        "maint_mode": 0,
        "visitor_tracking": 0,
        "use_fancy_urls": 0,
        "enable_plugins": False,
        "enable_delete_old_users": 0,
        "users_delete_after_days": 15,
        "email_after_days": 45,
        "use_smtp": initial_outbound_email_enabled(),
        "smtp_host": None,
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_user": None,
        "smtp_pass": None,
        "smtp_default_sender": None,
        "enable_analytics": False,
        "allow_custom_themes": False,
        "enable_logging": True,
        "log_level": "INFO"
    }

    env = EnvSettings(**settings_data)
    db.session.add(env)
    db.session.commit()
    logger.info(f"Global EnvSettings initialized for user_id {admin_user.id}.")


def seed_bundled_plugins():
    """Ensure plugins shipped with this build exist in the deployment registry.

    Bundled applications are registered disabled and unconfigured on first seed.
    Existing registrations are left untouched so administrator-requested enabled
    state and the last validated configuration result survive repeat hydration.
    Runtime loading remains entirely DB-driven.
    """

    added = 0
    for bundled in bundled_plugin_registrations():
        by_id = db.session.scalar(
            select(PluginRegistration).filter_by(plugin_id=bundled.plugin_id)
        )
        if by_id is not None:
            if by_id.import_path != bundled.import_path:
                raise ValueError(
                    f"Bundled plugin {bundled.plugin_id!r} is registered from "
                    f"unexpected path {by_id.import_path!r}"
                )
            continue

        by_path = db.session.scalar(
            select(PluginRegistration).filter_by(import_path=bundled.import_path)
        )
        if by_path is not None:
            raise ValueError(
                f"Bundled plugin path {bundled.import_path!r} is already registered "
                f"as {by_path.plugin_id!r}"
            )

        db.session.add(
            PluginRegistration(
                plugin_id=bundled.plugin_id,
                import_path=bundled.import_path,
                enabled=False,
                configured=False,
            )
        )
        added += 1

    db.session.commit()
    logger.info(
        "Bundled application plugins verified/seeded; added=%d total=%d",
        added,
        len(bundled_plugin_registrations()),
    )


def _iso_data_path(filename):
    return Path(__file__).resolve().parents[1] / "data" / filename


def _load_iso_records(filename, root_key):
    path = _iso_data_path(filename)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    records = payload.get(root_key)
    if not isinstance(records, list):
        raise ValueError(f"Invalid ISO reference data in {path}: missing {root_key!r} list")
    return records


def seed_countries():
    """Synchronize the host ISO 3166-1 country reference catalog."""
    records = _load_iso_records("iso_3166-1.json", "3166-1")
    existing = {country.iso_code_2: country for country in Country.query.all()}
    seen = set()
    inserted = 0
    updated = 0

    for record in records:
        iso_code_2 = str(record.get("alpha_2", "")).strip().upper()
        iso_code_3 = str(record.get("alpha_3", "")).strip().upper()
        name = str(record.get("name", "")).strip()
        if len(iso_code_2) != 2 or len(iso_code_3) != 3 or not name:
            raise ValueError(f"Invalid ISO 3166-1 record: {record!r}")

        seen.add(iso_code_2)
        country = existing.get(iso_code_2)
        if country is None:
            db.session.add(
                Country(
                    name=name,
                    iso_code_2=iso_code_2,
                    iso_code_3=iso_code_3,
                    active=True,
                )
            )
            inserted += 1
            continue

        changed = False
        for attribute, value in (("name", name), ("iso_code_3", iso_code_3), ("active", True)):
            if getattr(country, attribute) != value:
                setattr(country, attribute, value)
                changed = True
        if changed:
            updated += 1

    deactivated = 0
    for iso_code_2, country in existing.items():
        if iso_code_2 not in seen and country.active:
            country.active = False
            deactivated += 1

    db.session.commit()
    logger.info(
        "ISO countries synchronized: inserted=%d updated=%d deactivated=%d active=%d",
        inserted,
        updated,
        deactivated,
        len(seen),
    )


def seed_zones():
    """Synchronize ISO 3166-2 subdivisions and resolve parent relationships."""
    records = _load_iso_records("iso_3166-2.json", "3166-2")
    countries = {country.iso_code_2: country for country in Country.query.all()}
    existing = {zone.code: zone for zone in Zone.query.all()}
    seen = set()
    parent_codes = {}
    inserted = 0
    updated = 0
    parents_updated = 0

    for record in records:
        code = str(record.get("code", "")).strip().upper()
        name = str(record.get("name", "")).strip()
        zone_type = str(record.get("type", "")).strip() or None
        parent_code = str(record.get("parent", "")).strip().upper() or None
        if "-" not in code or not name:
            raise ValueError(f"Invalid ISO 3166-2 record: {record!r}")

        country_code = code.split("-", 1)[0]
        country = countries.get(country_code)
        if country is None:
            raise ValueError(
                f"ISO 3166-2 record {code!r} references unknown country {country_code!r}"
            )

        seen.add(code)
        parent_codes[code] = parent_code
        zone = existing.get(code)
        if zone is None:
            zone = Zone(
                country_id=country.country_id,
                code=code,
                name=name,
                type=zone_type,
                active=True,
            )
            db.session.add(zone)
            existing[code] = zone
            inserted += 1
            continue

        changed = False
        values = (
            ("country_id", country.country_id),
            ("name", name),
            ("type", zone_type),
            ("active", True),
        )
        for attribute, value in values:
            if getattr(zone, attribute) != value:
                setattr(zone, attribute, value)
                changed = True
        if changed:
            updated += 1

    # Materialize generated primary keys before resolving the self-reference.
    db.session.flush()

    for code, parent_code in parent_codes.items():
        zone = existing[code]
        if parent_code and parent_code not in parent_codes:
            raise ValueError(f"ISO zone {code!r} references non-current parent {parent_code!r}")
        parent = existing.get(parent_code) if parent_code else None
        if parent_code and parent is None:
            raise ValueError(f"ISO zone {code!r} references unknown parent {parent_code!r}")
        if parent is not None and parent.country_id != zone.country_id:
            raise ValueError(f"ISO zone {code!r} has cross-country parent {parent_code!r}")
        parent_zone_id = parent.zone_id if parent is not None else None
        if zone.parent_zone_id != parent_zone_id:
            zone.parent_zone_id = parent_zone_id
            parents_updated += 1

    deactivated = 0
    for code, zone in existing.items():
        if code not in seen and zone.active:
            zone.active = False
            deactivated += 1

    db.session.commit()
    logger.info(
        "ISO zones synchronized: inserted=%d updated=%d parents_updated=%d deactivated=%d active=%d",
        inserted,
        updated,
        parents_updated,
        deactivated,
        len(seen),
    )


def run_all_seeds():
    """
    Orchestrator for the boot-time hydration.
    """
    try:
        seed_roles()
        seed_countries()
        seed_zones()
        seed_admin_user()
        seed_env_settings()
        seed_bundled_plugins()
        logger.info("Database Hydration Complete.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database Hydration Failed: {str(e)}")
        raise


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        run_all_seeds()
