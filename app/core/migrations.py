# core/migrations.py

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
