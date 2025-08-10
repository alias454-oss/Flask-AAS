# app/core/meta.py
import logging
from app.core.cache import get_cached_env_settings
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_app_name():
    try:
        env = get_cached_env_settings()
        return env.get("site_name") or settings.APP_NAME or "Default App"
    except RuntimeError:
        # fallback if accessed outside app context
        logger.warning("No app context; using settings.APP_NAME or fallback")
        return settings.APP_NAME or "Default App"

APP_NAME = get_app_name()

page_metadata = {
    "dashboard": {
        "title": "User Dashboard",
        "description": f"Manage your {APP_NAME} account, view user data, and access personalized features.",
        "keywords": "user dashboard, account settings, user data, activity overview"
    },
    "account": {
        "title": "User Account",
        "description": f"View and manage your personal account details, preferences, and security settings on {APP_NAME}.",
        "keywords": "user account, profile management, account settings, personal information, security preferences"
    },
    "index": {
        "title": "Home",
        "description": f"A clean, extendable starter template for {APP_NAME} Flask web applications.",
        "keywords": "flask starter, login system, web app template, registration"
    },
    "about": {
        "title": "About",
        "description": f"Learn more about the {APP_NAME} web app template and its core features.",
        "keywords": "about this site, flask app, web app info, starter template"
    },
    "contact": {
        "title": "Contact Us",
        "description": f"Send us a message or inquiry through the {APP_NAME} contact form.",
        "keywords": "contact, feedback, questions, support, help"
    },
    "privacy": {
        "title": "Privacy Policy",
        "description": f"Understand how {APP_NAME} collects, uses, and protects your personal data.",
        "keywords": "privacy policy, data protection, user data, information security, GDPR, CCPA"
    },
    "tos": {
        "title": "Terms of Service",
        "description": f"Review the legal terms and acceptable use policies for accessing and using {APP_NAME}.",
        "keywords": "terms of service, user agreement, site rules, legal, acceptable use"
    },
    "login": {
        "title": "Login",
        "description": f"Secure login to access your {APP_NAME} dashboard and personalized tools.",
        "keywords": "user login, flask authentication, sign in, secure access"
    },
    "register": {
        "title": "Register",
        "description": f"Create a new {APP_NAME} account to start using the application features.",
        "keywords": "user registration, sign up, create account, new user"
    },
    "reset": {
        "title": "Reset Password",
        "description": f"Securely reset your {APP_NAME} account password using a verification link sent to your email.",
        "keywords": "password reset, forgot password, account recovery, secure login, change password"
    },
    "verify": {
        "title": "Verify Account",
        "description": f"Complete the verification process to activate your {APP_NAME} account and access full features.",
        "keywords": "account verification, email confirmation, user activation, secure login, verify identity"
    },
    "mfa": {
        "title": "Multi-Factor Authentication",
        "description": f"Enhance your {APP_NAME} account security by managing your Multi-Factor Authentication settings and devices.",
        "keywords": "multi-factor authentication, MFA, two-factor authentication, account security, 2FA, security settings"
    },
    "admin": {
        "title": "Admin Dashboard",
        "description": f"Site-wide administrative control panel for {APP_NAME}. Manage users, monitor activity, and adjust settings.",
        "keywords": "admin, site settings, user management, dashboard"
    },
    "admin_settings": {
        "title": "Admin Site Settings",
        "description": f"Configure core {APP_NAME} settings and manage operational preferences.",
        "keywords": "site configuration, admin tools, flask admin, app settings"
    },
    "admin_users": {
        "title": "Admin User Management",
        "description": f"Manage user accounts, roles, and access controls across the {APP_NAME} platform.",
        "keywords": "user management, admin users, role-based access, account control, user permissions"
    }
}
