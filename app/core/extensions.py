# app/core/extensions.py
"""Flask extension instances plus core schema/migration ownership helpers."""

from flask_caching import Cache
from flask_limiter import Limiter
from flask_mailman import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

# Core lifecycle metadata intentionally shares the plugin_ prefix. All other
# plugin_<id>_* tables belong to the plugin migration history, not core Alembic.
CORE_PLUGIN_TABLES = frozenset({"plugin_registrations"})


def core_migration_owns_table(name: str | None) -> bool:
    """Return whether a table belongs to the Flask-AAS core migration history."""
    if not name or not name.startswith("plugin_"):
        return True
    return name in CORE_PLUGIN_TABLES


def core_migration_include_name(name, type_, parent_names):
    """Exclude plugin-owned database objects before Alembic reflects them."""
    if type_ == "table":
        return core_migration_owns_table(name)

    table_name = (parent_names or {}).get("table_name")
    if table_name is not None:
        return core_migration_owns_table(table_name)
    return True


def core_migration_include_object(obj, name, type_, reflected, compare_to):
    """Keep core Alembic autogenerate out of plugin-owned table namespaces."""
    if type_ == "table":
        return core_migration_owns_table(name)

    table = getattr(obj, "table", None)
    if table is not None:
        return core_migration_owns_table(table.name)
    return True


def _client_ip_key():
    # Lazy import avoids an extensions/security import cycle at module load time.
    from app.core.security import get_client_ip

    return get_client_ip()


limiter = Limiter(
    key_func=_client_ip_key,
    default_limits=["500 per minute"],
)

db = SQLAlchemy()
migrate = Migrate(
    include_name=core_migration_include_name,
    include_object=core_migration_include_object,
)
csrf = CSRFProtect()
cache = Cache()
mail = Mail()


def table_exists(table_name: str) -> bool:
    """Return whether a table exists in the configured application database.

    This is intentionally a small host-level readiness primitive. Callers that
    depend on DB-backed configuration can use it during greenfield bootstrap to
    avoid issuing ORM queries against tables that migrations have not created
    yet. Database connectivity or inspection failures fail closed as ``False``.
    """

    try:
        return inspect(db.engine).has_table(table_name)
    except SQLAlchemyError:
        return False
