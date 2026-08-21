# Development and Deployment Modes

Flask-AAS is intentionally easy to run locally and stricter where deployment topology requires it.

The core rule is:

```text
low-friction development
+ explicit deployment boundaries
+ validation where trust changes
```

## Configuration matrix

| Capability | Local development | Production / multi-worker |
|---|---|---|
| Application URL | Local HTTP is valid | Explicit canonical external URL |
| TLS | Not required | Required at the external boundary |
| `SECRET_KEY` | Generated/local fallback allowed | Stable externally supplied shared key |
| Secure cookies | Off for HTTP | On for HTTPS |
| `HttpOnly` | On | On |
| `SameSite` | Normally `Lax` | Policy-defined, normally `Lax` |
| HSTS | Not required for direct HTTP | Enable at an HTTPS boundary |
| `ProxyFix` | Off unless using a known proxy | Explicit hop count matching topology |
| Host validation | Localhost-friendly | Canonical/allowed host required |
| Rate-limit/cache state | In-memory is acceptable for one process | Shared backend for multiple workers/instances |
| Database | SQLite supported | PostgreSQL is the validated production backend |
| Email | Optional | Configure when email-dependent features are enabled |
| Bootstrap admin | Fresh seeded admin does not require forced password replacement | Fresh seeded admin must replace the provisioned password after complete authentication |
| Media | Local directory | Durable storage appropriate to topology |
| Plugins | Optional | Explicitly enabled and trusted |

Internal TLS between a trusted reverse proxy and Flask/Gunicorn is topology-dependent. Flask-AAS
does not require certificates for an isolated internal HTTP hop.

## Secret key

Local development may generate or load a local secret when `SECRET_KEY` is absent.

Production and multi-worker deployments must use one stable shared key. Per-process or per-restart
keys invalidate sessions and signed tokens unpredictably.

Never log the secret.

## Site URL and host validation

`SITE_URL` is the clean-install seed for the public application origin. The default supports direct
local development:

```text
http://127.0.0.1:5000
```

After `EnvSettings` exists, **Admin → Site Settings → Site URL** becomes the persisted source.

At startup Flask-AAS derives:

```text
site_url / SITE_URL
        |
        +--> SERVER_NAME
        +--> PREFERRED_URL_SCHEME
        +--> TRUSTED_HOSTS
```

Changing Site URL is a startup-bound trust change and requires an application restart or reload.

Loopback configuration permits the common `127.0.0.1`, `::1`, and `localhost` aliases.

## Reverse proxies

Direct development should leave `PROXY_HOPS=0`. Forwarding headers are then ignored for effective
client identity.

When a trusted reverse proxy is used:

- set `PROXY_HOPS` to the actual forwarded origin-header hop count;
- configure `TRUSTED_PROXIES` for the immediate trusted peer or narrowly scoped proxy networks;
- configure the proxy to overwrite/sanitize `X-Forwarded-For` and `X-Real-IP`;
- prevent untrusted direct access to the Gunicorn port as defense in depth;
- keep Host validation independent from client-IP trust.

Flask-AAS applies forwarded host/protocol/prefix values only when the immediate network peer is in
`TRUSTED_PROXIES`. Effective client identity is resolved separately from the trusted
`X-Forwarded-For` chain (with `X-Real-IP` as a single-value fallback), and the same identity is used
for audit metadata, authentication lockout/rate-limit keys, and the global Flask-Limiter key.
Untrusted peers cannot opt into proxy behavior by supplying forwarding headers.

Do not enable proxy trust generically just because the application is deployed.

## Cookies and HSTS

Secure cookies cannot work over direct HTTP and should remain off for that development mode.
`HttpOnly` and an appropriate `SameSite` policy should remain enabled.

HSTS belongs at an HTTPS boundary. A direct HTTP development response should not be treated as an
HTTPS security boundary.

## Session inactivity

`SESSION_INACTIVITY_TIMEOUT_SECONDS` controls the sliding authenticated-session inactivity window.
The default is 900 seconds; `0` disables it.

When the boundary is crossed:

- a normal session is fully logged out;
- a remembered session retains its remember identity but is downgraded to non-fresh authentication;
- the boundary-crossing request is stopped with HTTP `303`, preventing a stale state-changing
  request from continuing.

Static requests do not extend the inactivity window. Password changes, session revocation, MFA, and
fresh-login requirements remain separate controls.

## Password policy

Deployment `PASSWORD_*` values seed a clean database. Once `EnvSettings` exists,
**Admin → Site Settings → Password Policy** is authoritative.

The default minimum length is 20 characters. Long passphrases and spaces are valid. Password
generation and user-selected passwords use the same active policy.

## Bootstrap administrator

A newly seeded bootstrap administrator uses `ADMIN_SECRET` only when the `admin` account does not
already exist.

In production, the seeded credential is provisioned state: `must_change_password=True`. The
administrator completes normal password authentication and any required MFA, then chooses a private
replacement password before normal navigation is allowed.

In development and testing, the bootstrap seeder sets `must_change_password=False` so repeatedly
creating clean disposable instances does not require a password-change ceremony. This exception is
limited to the bootstrap administrator; users created by an administrator with an explicit password
still receive the normal forced-change requirement.

Reseeding or restarting an existing database does not reset the bootstrap administrator password or
forced-change state.

## Shared state

In-memory caches, lockout state, and rate limits are acceptable for a single development process.

Multiple workers or instances require shared state where enforcement must be consistent across
processes. Redis is one possible backend; it is not a mandatory local dependency.

## Database and clean bootstrap

SQLite is supported for local development. PostgreSQL is the validated production/integration
backend.

A completely empty database is inspected before persisted Site Settings or plugin state are read.
During greenfield bootstrap, messages such as:

```text
Application plugin loader deferred; core schema is not initialized
Core schema not initialized; using default log level
```

are expected.

An error such as `relation "env_settings" does not exist` during that phase indicates a bootstrap
regression.

### Compose

Default development path:

```bash
docker compose up
```

PostgreSQL path:

```bash
docker compose \
  -f compose.yml \
  -f compose.postgres.yml \
  up
```

The repository image runs as the unprivileged `flaskaas` user and installs the hash-pinned runtime
lock with `--require-hashes` and `--only-binary=:all:`.

The Compose files are integration helpers, not a production hardening recipe.

## Migrations

The repository now ships a durable core migration baseline. A clean checkout uses the shipped
migration history:

```bash
python manage.py db upgrade
python manage.py seed-db
```

Do not run `db init` or generate a new "initial" migration merely to bootstrap a normal checkout.

Flask-AAS uses **rolled-up release checkpoints** for migration history. A released/supported
checkpoint remains a durable upgrade origin. Development-only revisions created after the latest
released checkpoint may be consolidated before the next release so the permanent history represents
the net schema change between supported checkpoints rather than every intermediate development edit.

For example, if v3 is the last released schema and v4/v5 development creates several provisional
revisions, the v5 release may publish one reviewed migration from the v3 checkpoint to the v5 schema.
The released v3 checkpoint remains intact; the unpublished intermediate revisions do not need to
become permanent history.

Before consolidating development-only history, back up the current database and migration tree.
Rebuild the rolled-up migration, re-identify/stamp the known-equivalent development database at the
new head, run the relevant regression suite, and remove the backups only after validation succeeds.

Application plugins have an independent migration boundary. A plugin may declare a package-local
migration environment in `plugin.toml`.

The host migration manager:

- isolates plugin tables under `plugin_<id>_*`;
- uses `plugin_<id>_alembic_version`;
- allows a fresh namespace to create current plugin-owned tables and stamp the plugin head;
- upgrades an existing versioned plugin through its own Alembic history;
- fails closed when plugin-owned tables exist without an expected version table;
- never migrates plugin schema merely because the plugin was enabled.

Released plugin migration checkpoints are durable upgrade origins. Unreleased plugin revisions after
the latest released checkpoint may be rolled up before the next release using the same backup,
re-identification, and regression-validation process.

## Profile-image storage

Host profile images default to:

```text
uploads/users
```

Relative paths resolve from the project root; absolute paths are used as configured.

The selected path must be writable. Container deployments should mount durable storage at a suitable
location, such as the top-level `uploads` directory.

A multi-instance deployment that accepts profile-image writes must provide storage visible to every
instance or introduce an explicit shared/object-storage design.

## Application-plugin hosting

Application plugins are optional. With the global plugin switch disabled, Flask-AAS continues to
provide its normal authentication, account, audit, contact, and administrative core.

Discovery reads immediate-child `app/plugins/*/plugin.toml` metadata without importing plugin
implementation code. Newly discovered plugins start disabled.

The important boundaries are:

```text
filesystem presence != registration != enablement != migration != configuration != activation
```

Enablement trusts the selected Python plugin to execute. It does not grant permission to migrate
schema.

Schema/configuration changes and structural plugin activation are reconciled through a fresh
Gunicorn worker using **Reload App Config**. A newly disabled plugin is denied immediately by the
host guard and removed structurally after reload.

Plugin acquisition remains operator-owned; Flask-AAS does not clone, upload, or install plugin
source.

See [`plugin-troubleshooting.md`](plugin-troubleshooting.md).

## Email

Development can run without SMTP.

Outbound email is controlled by the database-backed **Enable Outbound Email** setting. Effective
transport is resolved in this order:

1. disabled master switch;
2. `MAIL_DEBUG=true` mock delivery;
3. complete Site Settings SMTP override when enabled;
4. complete deployment SMTP configuration;
5. unavailable.

Site Settings and deployment SMTP values are not blended field by field. Partial runtime overrides
are rejected.

UI-managed SMTP passwords are encrypted with `MAIL_CONFIG_ENCRYPTION_KEY`, which must remain outside
the database.

Features that require email, such as required email verification, must have an effective outbound
transport.

## Production checklist

Before exposing Flask-AAS publicly, verify:

- stable `SECRET_KEY`;
- canonical `SITE_URL`;
- HTTPS at the external boundary;
- correct proxy hop and trusted-peer configuration;
- durable PostgreSQL storage;
- durable media storage;
- shared security state when using multiple workers/instances;
- working backups;
- the production bootstrap administrator has replaced any provisioned bootstrap password;
- outbound-mail configuration for enabled mail-dependent features;
- full test suite plus a clean PostgreSQL/bootstrap smoke test.
