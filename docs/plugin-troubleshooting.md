# Application Plugin Troubleshooting

This document covers operator/developer troubleshooting for the Flask-AAS Plugin API v1 lifecycle, especially cases where the filesystem, persisted plugin registry, migration state, configuration state, and running worker no longer describe the same application.

The key rule is:

```text
filesystem/package presence
    != persisted PluginRegistration
    != administrator enablement
    != schema readiness
    != configuration readiness
    != running-worker activation
    != optional Application Data readiness
```

Do not repair one layer by blindly deleting another.

## Quick state model

A normal application plugin moves through distinct layers:

1. A trusted plugin repository exists beneath `app/plugins/<id>/`.
2. `plugin.toml` is discovered without importing the plugin implementation.
3. Flask-AAS persists a `PluginRegistration` row.
4. The administrator explicitly enables the application.
5. The plugin schema is checked and, when required, explicitly upgraded.
6. Plugin-owned configuration is validated.
7. **Reload App Config** starts a fresh worker and reconciles structural runtime state.
8. Optional Application Data may be initialized independently.

A problem at one layer can look like a problem at another. Check them separately.

## Inspect persisted plugin registrations

`PluginRegistration` is durable host state. A plugin directory rename or removal does **not** automatically rename or delete the corresponding database row.

For a development installation, inspect the current registry with:

```bash
python - <<'PY'
from app import create_app
from app.models import PluginRegistration

app = create_app()

with app.app_context():
    for row in PluginRegistration.query.order_by(PluginRegistration.plugin_id):
        print(
            row.id,
            row.plugin_id,
            row.import_path,
            f"enabled={row.enabled}",
            f"configured={row.configured}",
        )
PY
```

Compare those values with the immediate child directories under `app/plugins/` and each plugin's current `plugin.toml`.

## Incident: stale registration after a plugin rename

During the pre-Alpha OpenAuto -> AutoGrid360 rename, the filesystem package changed from:

```text
app/plugins/openauto
```

to:

```text
app/plugins/autogrid360
```

The existing development database still contained the old registration:

```text
plugin_id = "openauto"
import path referencing app.plugins.openauto
```

Startup therefore attempted to load the stale import path and reported:

```text
ModuleNotFoundError: No module named 'app.plugins.openauto'
```

The new package on disk was not the problem. The persisted registry and filesystem identity disagreed.

### Recovery used for that pre-release development database

Only the stale **registration row** was removed:

```bash
python - <<'PY'
from app import create_app
from app.core.extensions import db
from app.models import PluginRegistration

app = create_app()

with app.app_context():
    row = PluginRegistration.query.filter_by(plugin_id="openauto").one_or_none()
    if row is None:
        print("No stale plugin registration found: openauto")
    else:
        db.session.delete(row)
        db.session.commit()
        print("Removed stale plugin registration: openauto")
PY
```

Then normal discovery/seeding was run again:

```bash
python manage.py seed-db
```

The successful development recovery logged discovery of the replacement registration, including:

```text
Bundled application plugins verified/seeded; added=1 total=2
```

The newly discovered `autogrid360` registration correctly started **disabled**:

```text
Plugin autogrid360 disabled; runtime loading skipped
```

That was expected. The remaining flow was normal:

```text
Admin -> Applications
-> Enable AutoGrid360
-> Reload App Config
-> NEEDS_MIGRATION
-> Upgrade Database Schema
-> Reload App Config
-> ACTIVE
-> initialize/refresh optional Application Data as needed
```

The recovery did **not** require deleting plugin-owned schema or business data as part of the registration cleanup.

## When direct registration-row cleanup is acceptable

Direct deletion of a `PluginRegistration` row is a narrow recovery technique, not a normal plugin lifecycle operation.

It is reasonable when all of the following are true:

- the installation is disposable/pre-release development state;
- the old plugin identity is known to be obsolete;
- the mismatch was caused by a deliberate rename/move/rebuild;
- no supported compatibility promise exists for the old identity;
- you intend to let normal manifest discovery create the replacement registration;
- you understand that removing the host registration row does not migrate or remove plugin-owned tables/data.

Before making even that narrow change, back up any development database you care about.

## When **not** to delete the registration row

Do not use the rename recovery above as a shortcut for a released/published plugin.

For a plugin that operators may already depend on, an ID/package/namespace change can affect:

- `PluginRegistration`;
- the package import path;
- plugin-owned `plugin_<id>_*` tables;
- `plugin_<id>_alembic_version`;
- migration history;
- plugin configuration and managed secrets;
- business data;
- portable/exported identifiers;
- documented URLs or CLI names.

That requires an explicit compatibility/migration design. Deleting the registration row does not perform any of those migrations.

Likewise, do not drop plugin tables merely because a registration is stale. Registration state is host metadata; schema and business data have separate ownership and lifecycle.

## Greenfield host bootstrap versus plugin state

Plugin troubleshooting starts only after the Flask-AAS core schema is ready. On a completely empty SQLite or PostgreSQL database, current Flask-AAS startup first checks whether core tables exist before reading database-backed settings or persisted plugin registrations.

Expected pre-schema messages include:

```text
Application plugin loader deferred; core schema is not initialized
Core schema not initialized; using default log level
```

The entrypoint can then initialize/generate/upgrade the current pre-release core schema and run idempotent seeding. A PostgreSQL server error such as `relation "env_settings" does not exist` during this clean-bootstrap phase indicates that startup code bypassed the core schema-readiness boundary; do not misdiagnose it as a stale plugin registration or plugin migration failure.

The clean PostgreSQL path has been exercised through core bootstrap, seed, plugin discovery, explicit AutoGrid360 enablement, `NEEDS_MIGRATION`, plugin schema initialization, reload to `ACTIVE`, and optional Application Data actions.

## Common plugin states and what they mean

### Plugin is discovered but Disabled

This is normal for a newly discovered plugin.

Check **Admin -> Applications** and enable it deliberately. Discovery is not trust and must not auto-enable native Python code.

### Plugin is enabled but reports `NEEDS_MIGRATION`

This is also normal after enabling a plugin whose declared schema is not current.

Use the host-managed browser action or CLI:

```bash
python manage.py plugin run <plugin_id> db current
python manage.py plugin run <plugin_id> db upgrade
```

Then use **Reload App Config** so a fresh worker sees the new schema state.

Do not manually stamp existing unversioned plugin tables. Flask-AAS intentionally fails closed when plugin-owned tables exist without the expected version history.

### Plugin reports `NEEDS_CONFIGURATION`

Complete the plugin-owned configuration workflow, then **Reload App Config** if the running worker still carries a startup-time status snapshot.

Configuration readiness is separate from schema readiness.

### Plugin looks Active in persisted state but routes/navigation are stale

Structural Flask Blueprint registration is a worker-start boundary.

Use **Reload App Config** after enablement, schema upgrades, or startup-sensitive configuration changes. Already-loaded request/navigation gates can react to persisted `enabled/configured` changes immediately, but the runtime status snapshot and structural registration are reconciled by the fresh worker.

In direct `flask run` development, the production Gunicorn reload action may not exist. Restart the development server normally.

### Plugin is Active but optional reference/postal/application data is missing

Application Data is deliberately **not** plugin lifecycle state.

An application can be `ACTIVE` while an optional dataset still needs initialization or refresh. Use the plugin's declared dataset actions under **Admin -> Applications** or the plugin's documented CLI.

Do not change `configured` or migration state merely to represent dataset readiness.

### `ModuleNotFoundError` names an old plugin package

Check the persisted registry before reinstalling dependencies or editing imports.

If the missing module name is an old plugin ID/path that no longer exists under `app/plugins/`, inspect `PluginRegistration` for stale metadata. A filesystem rename does not rewrite the database.

### Existing plugin tables exist but the version table does not

Do not force-stamp them.

Plugin API v1 intentionally treats existing unversioned plugin-owned tables as ambiguous and fails closed. In disposable development state, rebuild the disposable plugin/database state deliberately. For released data, create a reviewed migration/adoption procedure.

## Safe troubleshooting order

Use this order to avoid destructive guesswork:

1. Verify the plugin directory and `plugin.toml`.
2. Inspect persisted `PluginRegistration`.
3. Compare `plugin_id` and `import_path` with the current manifest/package.
4. Check whether the registration is enabled.
5. Check the reported runtime state: `DISABLED`, `NEEDS_MIGRATION`, `NEEDS_CONFIGURATION`, `ACTIVE`, `INCOMPATIBLE`, or `ERROR`.
6. Check plugin migration state separately.
7. Complete plugin-owned configuration separately.
8. Reload/restart the worker when structural or startup-time state changed.
9. Initialize optional Application Data separately.
10. Delete or rewrite persistent state only after identifying exactly which ownership layer is stale.

## What not to do

Avoid these shortcuts:

- do not assume moving a plugin directory updates the database;
- do not enable a newly discovered plugin automatically;
- do not treat **Enable** as permission to migrate schema;
- do not blindly stamp unversioned plugin tables;
- do not delete plugin schema/business data to fix a host registration mismatch;
- do not treat optional Application Data as schema/configuration readiness;
- do not expect a running worker to hot-add or hot-remove Flask Blueprints;
- do not use direct registry-row deletion as a published-plugin upgrade strategy.

## Related documentation

- [`deployment-modes.md`](deployment-modes.md) — deployment and plugin runtime boundaries
- [`security-checklist.md`](security-checklist.md) — route and plugin-boundary review checklist
- [`security-tooling.md`](security-tooling.md) — regression/security automation baseline
- [`../README.md`](../README.md) — normal plugin activation and migration commands
