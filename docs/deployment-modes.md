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

- set `PROXY_HOPS` to the actual forwarding hop count;
- configure `TRUSTED_PROXIES` for the immediate trusted peer;
- prevent untrusted direct access to a Gunicorn port that accepts trusted forwarding headers;
- keep Host validation independent from client-IP trust.

Do not enable `ProxyFix` generically just because the application is deployed.

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

Core migrations currently support clean development/initial deployment. Until a durable core upgrade
history is published, do not treat generated development revisions as a supported in-place upgrade
contract.

Application plugins have an independent migration boundary. A plugin may declare a package-local
migration environment in `plugin.toml`.

The host migration manager:

- isolates plugin tables under `plugin_<id>_*`;
- uses `plugin_<id>_alembic_version`;
- allows a fresh namespace to create current plugin-owned tables and stamp the plugin head;
- upgrades an existing versioned plugin through its own Alembic history;
- fails closed when plugin-owned tables exist without an expected version table;
- never migrates plugin schema merely because the plugin was enabled.

Published plugin migration history should be treated as durable.

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
- outbound-mail configuration for enabled mail-dependent features;
- full test suite plus a clean PostgreSQL/bootstrap smoke test.
