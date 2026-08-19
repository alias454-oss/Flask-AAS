# Application Plugin Troubleshooting

Flask-AAS keeps plugin filesystem state, registration, schema, configuration, and runtime activation
separate.

The most important rule is:

```text
filesystem/package presence
    != persisted registration
    != administrator enablement
    != schema readiness
    != configuration readiness
    != running-worker activation
    != optional application data
```

Do not repair one layer by blindly deleting another.

## Normal lifecycle

1. Trusted plugin source exists beneath `app/plugins/<id>/`.
2. `plugin.toml` is discovered without importing plugin implementation code.
3. Flask-AAS persists a disabled `PluginRegistration`.
4. An administrator enables the plugin.
5. Schema readiness is checked and upgraded explicitly when required.
6. Plugin-owned configuration is validated.
7. **Reload App Config** starts a fresh worker and reconciles structural runtime state.
8. Optional application/reference data is initialized separately.

## Inspect registrations

For a development installation:

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

Compare the persisted `plugin_id` and `import_path` with the current plugin directory and
`plugin.toml`.

## Common states

### Discovered but disabled

This is normal for a newly discovered plugin.

Enable it deliberately under **Admin → Applications**. Discovery is not permission to execute
plugin code.

### `NEEDS_MIGRATION`

The plugin is enabled, but its declared schema is not current.

Inspect or upgrade it with:

```bash
python manage.py plugin run <plugin_id> db current
python manage.py plugin run <plugin_id> db upgrade
```

Then use **Reload App Config**.

Do not manually stamp existing unversioned plugin tables. Flask-AAS intentionally fails closed when
plugin-owned tables exist without the expected version history.

### `NEEDS_CONFIGURATION`

Complete the plugin-owned configuration workflow, then reload the application configuration when
startup-time state needs to be reconciled.

Configuration readiness is separate from schema readiness.

### Active registration but stale routes/navigation

Flask Blueprint registration is a worker-start boundary.

Use **Reload App Config** after enablement, schema upgrades, or startup-sensitive configuration
changes. Under direct `flask run` development, restart the development server normally.

### Active plugin but optional data is missing

Optional reference or application data is not plugin lifecycle state.

A plugin can be `ACTIVE` while a dataset still needs initialization. Use the plugin's declared
dataset action or documented CLI without changing schema/configuration state.

### `ModuleNotFoundError` references an old plugin package

Inspect `PluginRegistration` before changing dependencies or imports.

A plugin directory rename or removal does not rewrite persisted registration metadata.

### Plugin tables exist but the version table does not

Do not force-stamp them.

For disposable development data, rebuild deliberately. For data that must be preserved, create a
reviewed migration/adoption procedure.

## Development migration consolidation

Unreleased plugin migration revisions are development state, not permanent history merely because
they were generated. Flask-AAS plugins use rolled-up release checkpoints.

If the current development database already matches the intended new checkpoint:

1. back up the database and current migration directory;
2. remove only the unpublished/provisional migration history being consolidated;
3. recreate or regenerate the rolled-up migration from the last released checkpoint (or a new initial
   baseline before the first release);
4. re-identify/stamp the known-equivalent development database at the new head;
5. verify `db current` and run the complete plugin regression suite;
6. remove the backups only after validation succeeds.

A release checkpoint that real deployments may need to upgrade from is durable and must remain in the
history. Intermediate development revisions after that checkpoint may be replaced by one migration
representing the net schema change to the next release.

Direct version-table edits or equivalent stamping are development/release-engineering operations for
a schema already known to be equivalent. They are not a recovery technique for ambiguous,
unversioned, or unknown production tables.

## Renamed or removed plugin source

`PluginRegistration` is durable host state. Renaming:

```text
app/plugins/old_name
```

to:

```text
app/plugins/new_name
```

does not update the corresponding database row.

In disposable development state, deleting only a known-obsolete registration row can be a valid
recovery step:

```bash
python - <<'PY'
from app import create_app
from app.core.extensions import db
from app.models import PluginRegistration

app = create_app()

with app.app_context():
    row = PluginRegistration.query.filter_by(plugin_id="old_name").one_or_none()
    if row is None:
        print("No stale registration found")
    else:
        db.session.delete(row)
        db.session.commit()
        print("Removed stale registration")
PY
```

Then rerun normal discovery/seeding:

```bash
python manage.py seed-db
```

This removes **host registration metadata only**. It does not rename, migrate, or delete
plugin-owned schema, configuration, secrets, or business data.

For a published plugin identity, use an explicit compatibility/migration plan instead of direct row
cleanup.

## Greenfield database bootstrap

Plugin troubleshooting begins after the Flask-AAS core schema is ready.

On an empty database, expected early messages include:

```text
Application plugin loader deferred; core schema is not initialized
Core schema not initialized; using default log level
```

A database error such as `relation "env_settings" does not exist` during clean bootstrap is a core
bootstrap problem, not a stale-plugin problem.

## Safe troubleshooting order

1. Verify the plugin directory and `plugin.toml`.
2. Inspect persisted `PluginRegistration`.
3. Compare manifest/package identity with persisted metadata.
4. Confirm whether the registration is enabled.
5. Check runtime state: `DISABLED`, `NEEDS_MIGRATION`, `NEEDS_CONFIGURATION`, `ACTIVE`,
   `INCOMPATIBLE`, or `ERROR`.
6. Check migration state separately.
7. Complete plugin-owned configuration separately.
8. Reload/restart the worker when structural state changed.
9. Initialize optional application data separately.
10. Modify persistent state only after identifying which ownership layer is stale.

## Avoid

- auto-enabling newly discovered plugins;
- treating **Enable** as permission to migrate schema;
- blindly stamping unversioned plugin tables;
- deleting plugin tables to fix a registration mismatch;
- treating optional application data as configuration/schema readiness;
- expecting a running worker to hot-add or hot-remove Flask Blueprints;
- using direct registration-row deletion as a published-plugin upgrade strategy.

## Related documentation

- [`deployment-modes.md`](deployment-modes.md) — deployment and runtime boundaries
- [`security-checklist.md`](security-checklist.md) — route/plugin security review
- [`security-tooling.md`](security-tooling.md) — security automation baseline
- [`../README.md`](../README.md) — installation and normal plugin operation
