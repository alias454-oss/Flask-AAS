# app/core/seeder.py
import logging
from datetime import datetime, timezone
from sqlalchemy import select, func

from flask import current_app
from app import create_app, db
from app.models.country import Country
from app.models.state import State
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
        "country": 223,
        "address": "123 Somewhere",
        "city": "Some City",
        "state": "illinois",
        "zip": "61032",
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
        "site_url": "https://yoursite.com",
        "site_lang": "en",
        "site_timezone": "America/Chicago",
        "description": "Short description of site",
        "keywords": "Keyword, keyword one, separate, by commas",
        "admin_name": "admin",
        "admin_email": "admin@site.com",
        "site_mode": 1,  # 0 = public/multi-user, 1 = single-user
        "default_role_id": user_role.id if user_role else None,
        "users_per_page": 10,
        "users_stored_path": "/images/users",
        "max_failed_attempts": 5,
        "lockout_duration_seconds": 900,
        "password_policy_enabled": current_app.config.get("PASSWORD_POLICY_ENABLED", True),
        "password_min_length": current_app.config.get("PASSWORD_MIN_LENGTH", 20),
        "password_require_uppercase": current_app.config.get("PASSWORD_REQUIRE_UPPERCASE", False),
        "password_require_lowercase": current_app.config.get("PASSWORD_REQUIRE_LOWERCASE", False),
        "password_require_number": current_app.config.get("PASSWORD_REQUIRE_NUMBER", False),
        "password_require_special": current_app.config.get("PASSWORD_REQUIRE_SPECIAL", False),
        "template": "default",
        "use_verify_email": 0,
        "use_user_approval": 0,
        "use_user_location": 0,
        "use_captcha": 0,
        "contact_enabled": False,
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


def seed_countries():
    # Only seed if table is empty
    if db.session.scalar(select(func.count(Country.country_id))) > 0:
        logger.info("Countries already seeded.")
        return

    countries = [
        {"name": "Afghanistan", "iso_code_2": "AF", "iso_code_3": "AFG", "address_format": ""},
        {"name": "Åland Islands", "iso_code_2": "AX", "iso_code_3": "ALA", "address_format": ""},
        {"name": "Albania", "iso_code_2": "AL", "iso_code_3": "ALB", "address_format": ""},
        {"name": "Algeria", "iso_code_2": "DZ", "iso_code_3": "DZA", "address_format": ""},
        {"name": "American Samoa", "iso_code_2": "AS", "iso_code_3": "ASM", "address_format": ""},
        {"name": "Andorra", "iso_code_2": "AD", "iso_code_3": "AND", "address_format": ""},
        {"name": "Angola", "iso_code_2": "AO", "iso_code_3": "AGO", "address_format": ""},
        {"name": "Anguilla", "iso_code_2": "AI", "iso_code_3": "AIA", "address_format": ""},
        {"name": "Antarctica", "iso_code_2": "AQ", "iso_code_3": "ATA", "address_format": ""},
        {"name": "Antigua and Barbuda", "iso_code_2": "AG", "iso_code_3": "ATG", "address_format": ""},
        {"name": "Argentina", "iso_code_2": "AR", "iso_code_3": "ARG", "address_format": ""},
        {"name": "Armenia", "iso_code_2": "AM", "iso_code_3": "ARM", "address_format": ""},
        {"name": "Aruba", "iso_code_2": "AW", "iso_code_3": "ABW", "address_format": ""},
        {"name": "Australia", "iso_code_2": "AU", "iso_code_3": "AUS", "address_format": ""},
        {"name": "Austria", "iso_code_2": "AT", "iso_code_3": "AUT", "address_format": ""},
        {"name": "Azerbaijan", "iso_code_2": "AZ", "iso_code_3": "AZE", "address_format": ""},
        {"name": "Bahamas", "iso_code_2": "BS", "iso_code_3": "BHS", "address_format": ""},
        {"name": "Bahrain", "iso_code_2": "BH", "iso_code_3": "BHR", "address_format": ""},
        {"name": "Bangladesh", "iso_code_2": "BD", "iso_code_3": "BGD", "address_format": ""},
        {"name": "Barbados", "iso_code_2": "BB", "iso_code_3": "BRB", "address_format": ""},
        {"name": "Belarus", "iso_code_2": "BY", "iso_code_3": "BLR", "address_format": ""},
        {"name": "Belgium", "iso_code_2": "BE", "iso_code_3": "BEL", "address_format": ""},
        {"name": "Belize", "iso_code_2": "BZ", "iso_code_3": "BLZ", "address_format": ""},
        {"name": "Benin", "iso_code_2": "BJ", "iso_code_3": "BEN", "address_format": ""},
        {"name": "Bermuda", "iso_code_2": "BM", "iso_code_3": "BMU", "address_format": ""},
        {"name": "Bhutan", "iso_code_2": "BT", "iso_code_3": "BTN", "address_format": ""},
        {"name": "Bolivia", "iso_code_2": "BO", "iso_code_3": "BOL", "address_format": ""},
        {"name": "Bonaire, Sint Eustatius and Saba", "iso_code_2": "BQ", "iso_code_3": "BES", "address_format": ""},
        {"name": "Bosnia and Herzegovina", "iso_code_2": "BA", "iso_code_3": "BIH", "address_format": ""},
        {"name": "Botswana", "iso_code_2": "BW", "iso_code_3": "BWA", "address_format": ""},
        {"name": "Bouvet Island", "iso_code_2": "BV", "iso_code_3": "BVT", "address_format": ""},
        {"name": "Brazil", "iso_code_2": "BR", "iso_code_3": "BRA", "address_format": ""},
        {"name": "British Indian Ocean Territory", "iso_code_2": "IO", "iso_code_3": "IOT", "address_format": ""},
        {"name": "Brunei Darussalam", "iso_code_2": "BN", "iso_code_3": "BRN", "address_format": ""},
        {"name": "Bulgaria", "iso_code_2": "BG", "iso_code_3": "BGR", "address_format": ""},
        {"name": "Burkina Faso", "iso_code_2": "BF", "iso_code_3": "BFA", "address_format": ""},
        {"name": "Burundi", "iso_code_2": "BI", "iso_code_3": "BDI", "address_format": ""},
        {"name": "Cabo Verde", "iso_code_2": "CV", "iso_code_3": "CPV", "address_format": ""},
        {"name": "Cambodia", "iso_code_2": "KH", "iso_code_3": "KHM", "address_format": ""},
        {"name": "Cameroon", "iso_code_2": "CM", "iso_code_3": "CMR", "address_format": ""},
        {"name": "Canada", "iso_code_2": "CA", "iso_code_3": "CAN", "address_format": ""},
        {"name": "Cayman Islands", "iso_code_2": "KY", "iso_code_3": "CYM", "address_format": ""},
        {"name": "Central African Republic", "iso_code_2": "CF", "iso_code_3": "CAF", "address_format": ""},
        {"name": "Chad", "iso_code_2": "TD", "iso_code_3": "TCD", "address_format": ""},
        {"name": "Chile", "iso_code_2": "CL", "iso_code_3": "CHL", "address_format": ""},
        {"name": "China", "iso_code_2": "CN", "iso_code_3": "CHN", "address_format": ""},
        {"name": "Christmas Island", "iso_code_2": "CX", "iso_code_3": "CXR", "address_format": ""},
        {"name": "Cocos (Keeling) Islands", "iso_code_2": "CC", "iso_code_3": "CCK", "address_format": ""},
        {"name": "Colombia", "iso_code_2": "CO", "iso_code_3": "COL", "address_format": ""},
        {"name": "Comoros", "iso_code_2": "KM", "iso_code_3": "COM", "address_format": ""},
        {"name": "Congo", "iso_code_2": "CG", "iso_code_3": "COG", "address_format": ""},
        {"name": "Congo, Democratic Republic of the", "iso_code_2": "CD", "iso_code_3": "COD", "address_format": ""},
        {"name": "Cook Islands", "iso_code_2": "CK", "iso_code_3": "COK", "address_format": ""},
        {"name": "Costa Rica", "iso_code_2": "CR", "iso_code_3": "CRI", "address_format": ""},
        {"name": "Croatia", "iso_code_2": "HR", "iso_code_3": "HRV", "address_format": ""},
        {"name": "Cuba", "iso_code_2": "CU", "iso_code_3": "CUB", "address_format": ""},
        {"name": "Curaçao", "iso_code_2": "CW", "iso_code_3": "CUW", "address_format": ""},
        {"name": "Cyprus", "iso_code_2": "CY", "iso_code_3": "CYP", "address_format": ""},
        {"name": "Czechia", "iso_code_2": "CZ", "iso_code_3": "CZE", "address_format": ""},
        {"name": "Denmark", "iso_code_2": "DK", "iso_code_3": "DNK", "address_format": ""},
        {"name": "Djibouti", "iso_code_2": "DJ", "iso_code_3": "DJI", "address_format": ""},
        {"name": "Dominica", "iso_code_2": "DM", "iso_code_3": "DMA", "address_format": ""},
        {"name": "Dominican Republic", "iso_code_2": "DO", "iso_code_3": "DOM", "address_format": ""},
        {"name": "Ecuador", "iso_code_2": "EC", "iso_code_3": "ECU", "address_format": ""},
        {"name": "Egypt", "iso_code_2": "EG", "iso_code_3": "EGY", "address_format": ""},
        {"name": "El Salvador", "iso_code_2": "SV", "iso_code_3": "SLV", "address_format": ""},
        {"name": "Equatorial Guinea", "iso_code_2": "GQ", "iso_code_3": "GNQ", "address_format": ""},
        {"name": "Eritrea", "iso_code_2": "ER", "iso_code_3": "ERI", "address_format": ""},
        {"name": "Estonia", "iso_code_2": "EE", "iso_code_3": "EST", "address_format": ""},
        {"name": "Eswatini", "iso_code_2": "SZ", "iso_code_3": "SWZ", "address_format": ""},
        {"name": "Ethiopia", "iso_code_2": "ET", "iso_code_3": "ETH", "address_format": ""},
        {"name": "Falkland Islands (Malvinas)", "iso_code_2": "FK", "iso_code_3": "FLK", "address_format": ""},
        {"name": "Faroe Islands", "iso_code_2": "FO", "iso_code_3": "FRO", "address_format": ""},
        {"name": "Fiji", "iso_code_2": "FJ", "iso_code_3": "FJI", "address_format": ""},
        {"name": "Finland", "iso_code_2": "FI", "iso_code_3": "FIN", "address_format": ""},
        {"name": "France", "iso_code_2": "FR", "iso_code_3": "FRA", "address_format": ""},
        {"name": "French Guiana", "iso_code_2": "GF", "iso_code_3": "GUF", "address_format": ""},
        {"name": "French Polynesia", "iso_code_2": "PF", "iso_code_3": "PYF", "address_format": ""},
        {"name": "French Southern Territories", "iso_code_2": "TF", "iso_code_3": "ATF", "address_format": ""},
        {"name": "Gabon", "iso_code_2": "GA", "iso_code_3": "GAB", "address_format": ""},
        {"name": "Gambia", "iso_code_2": "GM", "iso_code_3": "GMB", "address_format": ""},
        {"name": "Georgia", "iso_code_2": "GE", "iso_code_3": "GEO", "address_format": ""},
        {"name": "Germany", "iso_code_2": "DE", "iso_code_3": "DEU", "address_format": ""},
        {"name": "Ghana", "iso_code_2": "GH", "iso_code_3": "GHA", "address_format": ""},
        {"name": "Gibraltar", "iso_code_2": "GI", "iso_code_3": "GIB", "address_format": ""},
        {"name": "Greece", "iso_code_2": "GR", "iso_code_3": "GRC", "address_format": ""},
        {"name": "Greenland", "iso_code_2": "GL", "iso_code_3": "GRL", "address_format": ""},
        {"name": "Grenada", "iso_code_2": "GD", "iso_code_3": "GRD", "address_format": ""},
        {"name": "Guadeloupe", "iso_code_2": "GP", "iso_code_3": "GLP", "address_format": ""},
        {"name": "Guam", "iso_code_2": "GU", "iso_code_3": "GUM", "address_format": ""},
        {"name": "Guatemala", "iso_code_2": "GT", "iso_code_3": "GTM", "address_format": ""},
        {"name": "Guinea", "iso_code_2": "GN", "iso_code_3": "GIN", "address_format": ""},
        {"name": "Guinea-Bissau", "iso_code_2": "GW", "iso_code_3": "GNB", "address_format": ""},
        {"name": "Guyana", "iso_code_2": "GY", "iso_code_3": "GUY", "address_format": ""},
        {"name": "Haiti", "iso_code_2": "HT", "iso_code_3": "HTI", "address_format": ""},
        {"name": "Heard Island and McDonald Islands", "iso_code_2": "HM", "iso_code_3": "HMD", "address_format": ""},
        {"name": "Holy See (Vatican City State)", "iso_code_2": "VA", "iso_code_3": "VAT", "address_format": ""},
        {"name": "Honduras", "iso_code_2": "HN", "iso_code_3": "HND", "address_format": ""},
        {"name": "Hong Kong", "iso_code_2": "HK", "iso_code_3": "HKG", "address_format": ""},
        {"name": "Hungary", "iso_code_2": "HU", "iso_code_3": "HUN", "address_format": ""},
        {"name": "Iceland", "iso_code_2": "IS", "iso_code_3": "ISL", "address_format": ""},
        {"name": "India", "iso_code_2": "IN", "iso_code_3": "IND", "address_format": ""},
        {"name": "Indonesia", "iso_code_2": "ID", "iso_code_3": "IDN", "address_format": ""},
        {"name": "Iran (Islamic Republic of)", "iso_code_2": "IR", "iso_code_3": "IRN", "address_format": ""},
        {"name": "Iraq", "iso_code_2": "IQ", "iso_code_3": "IRQ", "address_format": ""},
        {"name": "Ireland", "iso_code_2": "IE", "iso_code_3": "IRL", "address_format": ""},
        {"name": "Israel", "iso_code_2": "IL", "iso_code_3": "ISR", "address_format": ""},
        {"name": "Italy", "iso_code_2": "IT", "iso_code_3": "ITA", "address_format": ""},
        {"name": "Jamaica", "iso_code_2": "JM", "iso_code_3": "JAM", "address_format": ""},
        {"name": "Japan", "iso_code_2": "JP", "iso_code_3": "JPN", "address_format": ""},
        {"name": "Jordan", "iso_code_2": "JO", "iso_code_3": "JOR", "address_format": ""},
        {"name": "Kazakhstan", "iso_code_2": "KZ", "iso_code_3": "KAZ", "address_format": ""},
        {"name": "Kenya", "iso_code_2": "KE", "iso_code_3": "KEN", "address_format": ""},
        {"name": "Kiribati", "iso_code_2": "KI", "iso_code_3": "KIR", "address_format": ""},
        {"name": "Korea (Democratic People's Republic of)", "iso_code_2": "KP", "iso_code_3": "PRK", "address_format": ""},
        {"name": "Korea (Republic of)", "iso_code_2": "KR", "iso_code_3": "KOR", "address_format": ""},
        {"name": "Kosovo", "iso_code_2": "XK", "iso_code_3": "XKX", "address_format": ""},
        {"name": "Kuwait", "iso_code_2": "KW", "iso_code_3": "KWT", "address_format": ""},
        {"name": "Kyrgyzstan", "iso_code_2": "KG", "iso_code_3": "KGZ", "address_format": ""},
        {"name": "Lao People's Democratic Republic", "iso_code_2": "LA", "iso_code_3": "LAO", "address_format": ""},
        {"name": "Latvia", "iso_code_2": "LV", "iso_code_3": "LVA", "address_format": ""},
        {"name": "Lebanon", "iso_code_2": "LB", "iso_code_3": "LBN", "address_format": ""},
        {"name": "Lesotho", "iso_code_2": "LS", "iso_code_3": "LSO", "address_format": ""},
        {"name": "Liberia", "iso_code_2": "LR", "iso_code_3": "LBR", "address_format": ""},
        {"name": "Libya", "iso_code_2": "LY", "iso_code_3": "LBY", "address_format": ""},
        {"name": "Liechtenstein", "iso_code_2": "LI", "iso_code_3": "LIE", "address_format": ""},
        {"name": "Lithuania", "iso_code_2": "LT", "iso_code_3": "LTU", "address_format": ""},
        {"name": "Luxembourg", "iso_code_2": "LU", "iso_code_3": "LUX", "address_format": ""},
        {"name": "Macao", "iso_code_2": "MO", "iso_code_3": "MAC", "address_format": ""},
        {"name": "Madagascar", "iso_code_2": "MG", "iso_code_3": "MDG", "address_format": ""},
        {"name": "Malawi", "iso_code_2": "MW", "iso_code_3": "MWI", "address_format": ""},
        {"name": "Malaysia", "iso_code_2": "MY", "iso_code_3": "MYS", "address_format": ""},
        {"name": "Maldives", "iso_code_2": "MV", "iso_code_3": "MDV", "address_format": ""},
        {"name": "Mali", "iso_code_2": "ML", "iso_code_3": "MLI", "address_format": ""},
        {"name": "Malta", "iso_code_2": "MT", "iso_code_3": "MLT", "address_format": ""},
        {"name": "Marshall Islands", "iso_code_2": "MH", "iso_code_3": "MHL", "address_format": ""},
        {"name": "Martinique", "iso_code_2": "MQ", "iso_code_3": "MTQ", "address_format": ""},
        {"name": "Mauritania", "iso_code_2": "MR", "iso_code_3": "MRT", "address_format": ""},
        {"name": "Mauritius", "iso_code_2": "MU", "iso_code_3": "MUS", "address_format": ""},
        {"name": "Mayotte", "iso_code_2": "YT", "iso_code_3": "MYT", "address_format": ""},
        {"name": "Mexico", "iso_code_2": "MX", "iso_code_3": "MEX", "address_format": ""},
        {"name": "Micronesia, Federated States of", "iso_code_2": "FM", "iso_code_3": "FSM", "address_format": ""},
        {"name": "Moldova, Republic of", "iso_code_2": "MD", "iso_code_3": "MDA", "address_format": ""},
        {"name": "Monaco", "iso_code_2": "MC", "iso_code_3": "MCO", "address_format": ""},
        {"name": "Mongolia", "iso_code_2": "MN", "iso_code_3": "MNG", "address_format": ""},
        {"name": "Montenegro", "iso_code_2": "ME", "iso_code_3": "MNE", "address_format": ""},
        {"name": "Montserrat", "iso_code_2": "MS", "iso_code_3": "MSR", "address_format": ""},
        {"name": "Morocco", "iso_code_2": "MA", "iso_code_3": "MAR", "address_format": ""},
        {"name": "Mozambique", "iso_code_2": "MZ", "iso_code_3": "MOZ", "address_format": ""},
        {"name": "Myanmar", "iso_code_2": "MM", "iso_code_3": "MMR", "address_format": ""},
        {"name": "Namibia", "iso_code_2": "NA", "iso_code_3": "NAM", "address_format": ""},
        {"name": "Nauru", "iso_code_2": "NR", "iso_code_3": "NRU", "address_format": ""},
        {"name": "Nepal", "iso_code_2": "NP", "iso_code_3": "NPL", "address_format": ""},
        {"name": "Netherlands", "iso_code_2": "NL", "iso_code_3": "NLD", "address_format": ""},
        {"name": "New Caledonia", "iso_code_2": "NC", "iso_code_3": "NCL", "address_format": ""},
        {"name": "New Zealand", "iso_code_2": "NZ", "iso_code_3": "NZL", "address_format": ""},
        {"name": "Nicaragua", "iso_code_2": "NI", "iso_code_3": "NIC", "address_format": ""},
        {"name": "Niger", "iso_code_2": "NE", "iso_code_3": "NER", "address_format": ""},
        {"name": "Nigeria", "iso_code_2": "NG", "iso_code_3": "NGA", "address_format": ""},
        {"name": "Niue", "iso_code_2": "NU", "iso_code_3": "NIU", "address_format": ""},
        {"name": "Norfolk Island", "iso_code_2": "NF", "iso_code_3": "NFK", "address_format": ""},
        {"name": "Northern Mariana Islands", "iso_code_2": "MP", "iso_code_3": "MNP", "address_format": ""},
        {"name": "North Macedonia", "iso_code_2": "MK", "iso_code_3": "MKD", "address_format": ""},
        {"name": "Norway", "iso_code_2": "NO", "iso_code_3": "NOR", "address_format": ""},
        {"name": "Oman", "iso_code_2": "OM", "iso_code_3": "OMN", "address_format": ""},
        {"name": "Pakistan", "iso_code_2": "PK", "iso_code_3": "PAK", "address_format": ""},
        {"name": "Palau", "iso_code_2": "PW", "iso_code_3": "PLW", "address_format": ""},
        {"name": "Panama", "iso_code_2": "PA", "iso_code_3": "PAN", "address_format": ""},
        {"name": "Papua New Guinea", "iso_code_2": "PG", "iso_code_3": "PNG", "address_format": ""},
        {"name": "Paraguay", "iso_code_2": "PY", "iso_code_3": "PRY", "address_format": ""},
        {"name": "Peru", "iso_code_2": "PE", "iso_code_3": "PER", "address_format": ""},
        {"name": "Philippines", "iso_code_2": "PH", "iso_code_3": "PHL", "address_format": ""},
        {"name": "Pitcairn", "iso_code_2": "PN", "iso_code_3": "PCN", "address_format": ""},
        {"name": "Poland", "iso_code_2": "PL", "iso_code_3": "POL", "address_format": ""},
        {"name": "Portugal", "iso_code_2": "PT", "iso_code_3": "PRT", "address_format": ""},
        {"name": "Puerto Rico", "iso_code_2": "PR", "iso_code_3": "PRI", "address_format": ""},
        {"name": "Qatar", "iso_code_2": "QA", "iso_code_3": "QAT", "address_format": ""},
        {"name": "Reunion", "iso_code_2": "RE", "iso_code_3": "REU", "address_format": ""},
        {"name": "Romania", "iso_code_2": "RO", "iso_code_3": "ROU", "address_format": ""},
        {"name": "Russian Federation", "iso_code_2": "RU", "iso_code_3": "RUS", "address_format": ""},
        {"name": "Rwanda", "iso_code_2": "RW", "iso_code_3": "RWA", "address_format": ""},
        {"name": "Saint Kitts and Nevis", "iso_code_2": "KN", "iso_code_3": "KNA", "address_format": ""},
        {"name": "Saint Lucia", "iso_code_2": "LC", "iso_code_3": "LCA", "address_format": ""},
        {"name": "Saint Vincent and the Grenadines", "iso_code_2": "VC", "iso_code_3": "VCT", "address_format": ""},
        {"name": "Samoa", "iso_code_2": "WS", "iso_code_3": "WSM", "address_format": ""},
        {"name": "San Marino", "iso_code_2": "SM", "iso_code_3": "SMR", "address_format": ""},
        {"name": "Sao Tome and Principe", "iso_code_2": "ST", "iso_code_3": "STP", "address_format": ""},
        {"name": "Saudi Arabia", "iso_code_2": "SA", "iso_code_3": "SAU", "address_format": ""},
        {"name": "Senegal", "iso_code_2": "SN", "iso_code_3": "SEN", "address_format": ""},
        {"name": "Serbia", "iso_code_2": "RS", "iso_code_3": "SRB", "address_format": ""},
        {"name": "Seychelles", "iso_code_2": "SC", "iso_code_3": "SYC", "address_format": ""},
        {"name": "Sierra Leone", "iso_code_2": "SL", "iso_code_3": "SLE", "address_format": ""},
        {"name": "Singapore", "iso_code_2": "SG", "iso_code_3": "SGP", "address_format": ""},
        {"name": "Slovakia (Slovak Republic)", "iso_code_2": "SK", "iso_code_3": "SVK", "address_format": ""},
        {"name": "Slovenia", "iso_code_2": "SI", "iso_code_3": "SVN", "address_format": ""},
        {"name": "Solomon Islands", "iso_code_2": "SB", "iso_code_3": "SLB", "address_format": ""},
        {"name": "Somalia", "iso_code_2": "SO", "iso_code_3": "SOM", "address_format": ""},
        {"name": "South Africa", "iso_code_2": "ZA", "iso_code_3": "ZAF", "address_format": ""},
        {"name": "South Georgia and the South Sandwich Islands", "iso_code_2": "GS", "iso_code_3": "SGS", "address_format": ""},
        {"name": "Spain", "iso_code_2": "ES", "iso_code_3": "ESP", "address_format": ""},
        {"name": "Sri Lanka", "iso_code_2": "LK", "iso_code_3": "LKA", "address_format": ""},
        {"name": "St. Helena", "iso_code_2": "SH", "iso_code_3": "SHN", "address_format": ""},
        {"name": "St. Pierre and Miquelon", "iso_code_2": "PM", "iso_code_3": "SPM", "address_format": ""},
        {"name": "Sudan", "iso_code_2": "SD", "iso_code_3": "SDN", "address_format": ""},
        {"name": "South Sudan", "iso_code_2": "SS", "iso_code_3": "SSD", "address_format": ""},
        {"name": "Suriname", "iso_code_2": "SR", "iso_code_3": "SUR", "address_format": ""},
        {"name": "Svalbard and Jan Mayen Islands", "iso_code_2": "SJ", "iso_code_3": "SJM", "address_format": ""},
        {"name": "Sweden", "iso_code_2": "SE", "iso_code_3": "SWE", "address_format": ""},
        {"name": "Switzerland", "iso_code_2": "CH", "iso_code_3": "CHE", "address_format": ""},
        {"name": "Syria", "iso_code_2": "SY", "iso_code_3": "SYR", "address_format": ""},
        {"name": "Taiwan", "iso_code_2": "TW", "iso_code_3": "TWN", "address_format": ""},
        {"name": "Tajikistan", "iso_code_2": "TJ", "iso_code_3": "TJK", "address_format": ""},
        {"name": "Tanzania, United Republic of", "iso_code_2": "TZ", "iso_code_3": "TZA", "address_format": ""},
        {"name": "Thailand", "iso_code_2": "TH", "iso_code_3": "THA", "address_format": ""},
        {"name": "Timor-Leste", "iso_code_2": "TL", "iso_code_3": "TLS", "address_format": ""},
        {"name": "Togo", "iso_code_2": "TG", "iso_code_3": "TGO", "address_format": ""},
        {"name": "Tokelau", "iso_code_2": "TK", "iso_code_3": "TKL", "address_format": ""},
        {"name": "Tonga", "iso_code_2": "TO", "iso_code_3": "TON", "address_format": ""},
        {"name": "Trinidad and Tobago", "iso_code_2": "TT", "iso_code_3": "TTO", "address_format": ""},
        {"name": "Tunisia", "iso_code_2": "TN", "iso_code_3": "TUN", "address_format": ""},
        {"name": "Turkey", "iso_code_2": "TR", "iso_code_3": "TUR", "address_format": ""},
        {"name": "Turkmenistan", "iso_code_2": "TM", "iso_code_3": "TKM", "address_format": ""},
        {"name": "Turks and Caicos Islands", "iso_code_2": "TC", "iso_code_3": "TCA", "address_format": ""},
        {"name": "Tuvalu", "iso_code_2": "TV", "iso_code_3": "TUV", "address_format": ""},
        {"name": "Uganda", "iso_code_2": "UG", "iso_code_3": "UGA", "address_format": ""},
        {"name": "Ukraine", "iso_code_2": "UA", "iso_code_3": "UKR", "address_format": ""},
        {"name": "United Arab Emirates", "iso_code_2": "AE", "iso_code_3": "ARE", "address_format": ""},
        {"name": "United Kingdom", "iso_code_2": "GB", "iso_code_3": "GBR", "address_format": ""},
        {"name": "United States", "iso_code_2": "US", "iso_code_3": "USA", "address_format": ""},
        {"name": "United States Minor Outlying Islands", "iso_code_2": "UM", "iso_code_3": "UMI", "address_format": ""},
        {"name": "Uruguay", "iso_code_2": "UY", "iso_code_3": "URY", "address_format": ""},
        {"name": "Uzbekistan", "iso_code_2": "UZ", "iso_code_3": "UZB", "address_format": ""},
        {"name": "Vanuatu", "iso_code_2": "VU", "iso_code_3": "VUT", "address_format": ""},
        {"name": "Venezuela", "iso_code_2": "VE", "iso_code_3": "VEN", "address_format": ""},
        {"name": "Vietnam", "iso_code_2": "VN", "iso_code_3": "VNM", "address_format": ""},
        {"name": "Virgin Islands (British)", "iso_code_2": "VG", "iso_code_3": "VGB", "address_format": ""},
        {"name": "Virgin Islands (U.S.)", "iso_code_2": "VI", "iso_code_3": "VIR", "address_format": ""},
        {"name": "Wallis and Futuna Islands", "iso_code_2": "WF", "iso_code_3": "WLF", "address_format": ""},
        {"name": "Western Sahara", "iso_code_2": "EH", "iso_code_3": "ESH", "address_format": ""},
        {"name": "Yemen", "iso_code_2": "YE", "iso_code_3": "YEM", "address_format": ""},
        {"name": "Zambia", "iso_code_2": "ZM", "iso_code_3": "ZMB", "address_format": ""},
        {"name": "Zimbabwe", "iso_code_2": "ZW", "iso_code_3": "ZWE"}
    ]
    db.session.bulk_insert_mappings(Country, countries)
    db.session.commit()
    logger.info(f"Seeded {len(countries)} countries.")


def seed_states():
    if db.session.scalar(select(func.count(State.state_prefix))) > 0:
        logger.info("States already seeded.")
        return

    states = [
        {"state_prefix": "AL", "state_name": "Alabama"},
        {"state_prefix": "AK", "state_name": "Alaska"},
        {"state_prefix": "AS", "state_name": "American Samoa"},
        {"state_prefix": "AZ", "state_name": "Arizona"},
        {"state_prefix": "AR", "state_name": "Arkansas"},
        {"state_prefix": "CA", "state_name": "California"},
        {"state_prefix": "CO", "state_name": "Colorado"},
        {"state_prefix": "CT", "state_name": "Connecticut"},
        {"state_prefix": "DE", "state_name": "Delaware"},
        {"state_prefix": "DC", "state_name": "District of Columbia"},
        {"state_prefix": "FM", "state_name": "Federated States of Micronesia"},
        {"state_prefix": "FL", "state_name": "Florida"},
        {"state_prefix": "GA", "state_name": "Georgia"},
        {"state_prefix": "GU", "state_name": "Guam"},
        {"state_prefix": "HI", "state_name": "Hawaii"},
        {"state_prefix": "ID", "state_name": "Idaho"},
        {"state_prefix": "IL", "state_name": "Illinois"},
        {"state_prefix": "IN", "state_name": "Indiana"},
        {"state_prefix": "IA", "state_name": "Iowa"},
        {"state_prefix": "KS", "state_name": "Kansas"},
        {"state_prefix": "KY", "state_name": "Kentucky"},
        {"state_prefix": "LA", "state_name": "Louisiana"},
        {"state_prefix": "ME", "state_name": "Maine"},
        {"state_prefix": "MH", "state_name": "Marshall Islands"},
        {"state_prefix": "MD", "state_name": "Maryland"},
        {"state_prefix": "MA", "state_name": "Massachusetts"},
        {"state_prefix": "MI", "state_name": "Michigan"},
        {"state_prefix": "MN", "state_name": "Minnesota"},
        {"state_prefix": "MS", "state_name": "Mississippi"},
        {"state_prefix": "MO", "state_name": "Missouri"},
        {"state_prefix": "MT", "state_name": "Montana"},
        {"state_prefix": "NE", "state_name": "Nebraska"},
        {"state_prefix": "NV", "state_name": "Nevada"},
        {"state_prefix": "NH", "state_name": "New Hampshire"},
        {"state_prefix": "NJ", "state_name": "New Jersey"},
        {"state_prefix": "NM", "state_name": "New Mexico"},
        {"state_prefix": "NY", "state_name": "New York"},
        {"state_prefix": "NC", "state_name": "North Carolina"},
        {"state_prefix": "ND", "state_name": "North Dakota"},
        {"state_prefix": "MP", "state_name": "Northern Mariana Islands"},
        {"state_prefix": "OH", "state_name": "Ohio"},
        {"state_prefix": "OK", "state_name": "Oklahoma"},
        {"state_prefix": "OR", "state_name": "Oregon"},
        {"state_prefix": "PW", "state_name": "Palau"},
        {"state_prefix": "PA", "state_name": "Pennsylvania"},
        {"state_prefix": "PR", "state_name": "Puerto Rico"},
        {"state_prefix": "RI", "state_name": "Rhode Island"},
        {"state_prefix": "SC", "state_name": "South Carolina"},
        {"state_prefix": "SD", "state_name": "South Dakota"},
        {"state_prefix": "TN", "state_name": "Tennessee"},
        {"state_prefix": "TX", "state_name": "Texas"},
        {"state_prefix": "UT", "state_name": "Utah"},
        {"state_prefix": "VT", "state_name": "Vermont"},
        {"state_prefix": "VI", "state_name": "Virgin Islands"},
        {"state_prefix": "VA", "state_name": "Virginia"},
        {"state_prefix": "WA", "state_name": "Washington"},
        {"state_prefix": "WV", "state_name": "West Virginia"},
        {"state_prefix": "WI", "state_name": "Wisconsin"},
        {"state_prefix": "WY", "state_name": "Wyoming"}
    ]
    db.session.bulk_insert_mappings(State, states)
    db.session.commit()
    logger.info(f"Seeded {len(states)} states.")


def run_all_seeds():
    """
    Orchestrator for the boot-time hydration.
    """
    try:
        seed_roles()
        seed_admin_user()
        seed_env_settings()
        seed_bundled_plugins()
        seed_countries()
        seed_states()
        logger.info("Database Hydration Complete.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database Hydration Failed: {str(e)}")
        raise


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        run_all_seeds()
