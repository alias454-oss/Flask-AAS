# Application Plugin API

Flask-AAS can host trusted application plugins while remaining usable as a standalone
authentication/audit core.

Plugin hosting is optional and disabled globally by default.

## Trust model

Application plugins are native Python code.

A plugin enabled inside Flask-AAS executes with the permissions of the Flask-AAS process. The plugin
system is not a sandbox.

The important trust boundaries are intentionally separate:

```text
filesystem presence
    != registration
    != enablement
    != schema readiness
    != configuration readiness
    != runtime activation
    != optional application data
```

## Package layout

Deployment-supplied plugin repositories live beneath:

```text
app/plugins/<plugin_id>/
```

Each plugin exposes static metadata through an immediate-child `plugin.toml`.

Example:

```toml
[plugin]
id = "your_plugin"
name = "Your Plugin"
navigation_label = "Your Plugin"
entrypoint = "app.plugins.your_plugin.plugin:plugin"
migrations = "migrations"
```

Flask-AAS does not currently clone, upload, update, or provide a marketplace for plugin source.
Acquisition and placement are operator/deployment responsibilities.

## Discovery and registration

Startup discovery is metadata-only.

Flask-AAS reads the controlled `app/plugins/*/plugin.toml` boundary and can create/update compatible
`PluginRegistration` metadata without importing plugin implementation or model modules.

Newly discovered plugins are disabled by default.

Registration alone must not:

- execute plugin implementation code;
- create or migrate plugin schema;
- register plugin routes;
- contribute navigation;
- enable the application.

## Enablement

Administrator **Enable** is the deliberate native-code trust boundary.

Once enabled, Flask-AAS may import the selected plugin implementation and inspect its migration and
configuration readiness.

Enablement itself does **not** create, migrate, or stamp plugin schema.

## Runtime states

An enabled plugin may report:

- `NEEDS_MIGRATION`
- `NEEDS_CONFIGURATION`
- `ACTIVE`
- `INCOMPATIBLE`
- `ERROR`

A disabled plugin remains `DISABLED`.

A plugin with stale schema fails closed as `NEEDS_MIGRATION` before normal structural registration.

## Plugin migrations

Plugins own their schema separately from Flask-AAS core.

The default ownership convention is:

```text
plugin_<id>_*
plugin_<id>_alembic_version
```

The host migration manager:

- constrains migration/autogenerate to the plugin-owned table namespace;
- keeps plugin version state independent from core Alembic;
- can bootstrap a completely fresh plugin namespace from current plugin models and stamp the plugin
  head;
- runs normal Alembic history for an existing versioned plugin;
- fails closed when plugin-owned tables already exist without the expected plugin version table.

Do not manually stamp ambiguous existing tables.

When `plugin.toml` declares migrations, Flask-AAS provides:

```bash
python manage.py plugin run <plugin_id> db current
python manage.py plugin run <plugin_id> db upgrade
```

Plugin source development may additionally use:

```bash
python manage.py plugin run <plugin_id> db init
python manage.py plugin run <plugin_id> db migrate -m "Describe change"
python manage.py plugin run <plugin_id> db downgrade
```

Released plugin migration checkpoints are durable upgrade origins. Development-only revisions
created after the latest released checkpoint may be consolidated before the next release so the
permanent history represents the net schema change between supported checkpoints rather than every
intermediate model edit.

When consolidating unpublished plugin migration history, back up the current development database and
migration tree, regenerate the rolled-up migration from the intended supported origin, re-identify or
stamp the known-equivalent development database at the new head, run the plugin regression suite, and
remove the backups only after validation succeeds. Do not rewrite a released checkpoint that real
deployments may need as an upgrade origin.

## Configuration readiness

Configuration is plugin-owned.

A plugin may remain `NEEDS_CONFIGURATION` after its schema is ready. Complete the plugin's
configuration workflow, then reload the application configuration when startup-time state needs to be
reconciled.

Plugin domain settings should not be moved into Flask-AAS core merely for convenience.

## Reload boundary

Flask Blueprint structure is established when a worker starts.

**Reload App Config** reconciles structural plugin state by starting a fresh Gunicorn worker rather
than attempting live Blueprint mutation.

The container deployment uses a fixed `SIGHUP` to Gunicorn PID 1 after verifying that PID 1 is
Gunicorn. It does not invoke a shell or accept an arbitrary process/signal.

Under direct `flask run` development, restart the development server normally.

Already-loaded route/navigation guards can follow current persisted `enabled/configured` state
immediately, while the fresh worker reconciles startup-time status and structural registration.

## Disable behavior

Disabling a plugin:

- immediately denies effective plugin route/navigation access through the host guard;
- clears plugin-managed persisted secrets according to the plugin contract;
- preserves ordinary plugin configuration, schema, and business data;
- removes structural runtime registration after reload.

Disable is not uninstall and should not silently destroy business data.

## Optional application data

Reference datasets and other application data are not lifecycle state.

An application can be `ACTIVE` while optional data still needs initialization or refresh.

Plugins may expose dataset actions through the administrator interface or plugin CLI. Dataset
readiness should not be represented by changing migration/configuration state.

## CLI

Generic dispatch:

```bash
python manage.py plugin run <plugin_id> ...
```

The host logs plugin/command identity without treating arbitrary command arguments as safe audit
metadata.

CLI execution does not by itself make a plugin globally active in the web application.

## Authorization

Plugin route authorization remains application-owned.

A plugin route may be:

- intentionally public;
- authenticated through Flask-AAS;
- admitted by a coarse host role;
- protected by plugin-owned domain authorization.

The plugin should not create a parallel authentication stack merely because it needs application
authorization.

## Theme and navigation

Plugin pages inherit the host plugin template/theme baseline. Plugin CSS should remain additive and
domain-specific rather than duplicating host forms, typography, layout, focus behavior, and generic
controls.

Navigation contributions are host-integrated and remain subject to effective plugin state.

## Compatibility

`PLUGIN_API_VERSION = 1` is the current compatibility boundary.

Plugins should declare their supported host/API compatibility through static metadata so
incompatible applications can fail closed without taking down Flask-AAS core or unrelated plugins.

## Troubleshooting

For stale registrations, renamed plugin packages, missing schema, reload drift, and safe recovery
order, see [`plugin-troubleshooting.md`](plugin-troubleshooting.md).
