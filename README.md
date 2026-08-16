# Flask-AAS

Work In Progress — usable for development, testing, and controlled internal evaluation.

> **Pre-release status:** Flask-AAS is under active development. Review the documented configuration and deployment guidance before use.

Flask-AAS is a **modular Flask-based authentication, auditing, and application-host foundation** 
with built-in user management, session security, log tracking, optional abuse prevention, 
and an opt-in first-class application-plugin system. It is designed to stay understandable for 
small projects while providing a reusable security foundation for larger applications.

---

## Background & Philosophy

Flask Auth & Audit System began as authentication code written for Open Auto Classifieds more than a decade ago. 
As that code aged and my requirements changed, I decided to rebuild it in Flask rather than continue extending the original implementation.

Flask-AAS is built primarily to provide a consistent foundation for my own projects, but is published openly for anyone who finds it useful.

This project focuses on:
- Keeping external dependencies minimal
- Providing practical features that work out of the box
- Leaving optional integrations and extras up to you
- Hosting application-specific functionality behind a small, explicit plugin contract
- Staying adaptable for both small projects and larger ones

## Project Documentation

| Document | Purpose |
|---|---|
| [`docs/deployment-modes.md`](docs/deployment-modes.md) | Development versus deployed behavior |
| [`docs/security-checklist.md`](docs/security-checklist.md) | Reusable route-review checklist |
| [`docs/security-tooling.md`](docs/security-tooling.md) | Static analysis, dependency audit, and CI baseline |
| [`docs/plugin-troubleshooting.md`](docs/plugin-troubleshooting.md) | Plugin discovery, persisted registry, migration, reload, and recovery troubleshooting |

The base is intentionally designed to remain easy to run locally. Direct HTTP, generated development secrets, SQLite, and in-memory services are valid development choices. Stricter requirements apply only when the selected deployment mode needs them.

## Core Features

### Authentication & User Management
- Secure login with **Flask-Login**
- Password hashing via **Argon2id**, with transparent verification and login-time upgrade of legacy Flask-Bcrypt hashes
- Admin-managed password policy enabled by default with a 20-character minimum, passphrase-friendly composition defaults, and deployment values used only to seed a fresh database
- Optional provider-backed new-password checking, disabled by default, with a built-in local common-password blocklist and a registration seam for additional providers
- Active session tracking
- Sliding authenticated-session inactivity timeout; ordinary sessions require a full login after expiry
- Remembered sessions survive inactivity only as non-fresh authentication, preserving the remember cookie while requiring reauthentication for fresh-login-protected actions
- Remember-cookie-restored sessions begin a new non-fresh inactivity window
- Account state flags:
  - `activated` → Email verification status
  - `approved` → Optional admin review
- Role-based access control (RBAC)
- Flexible registration fields (company, phone, location, etc.)
- Self-service account profile editing with canonical host profile-image upload/replace/remove using the existing `User.image` identity field
- Shared ISO 3166-1 country and ISO 3166-2 subdivision catalog for consistent host/plugin location fields
- Admin panel with settings management
- Single-user lockdown mode
- Global CSRF protection

### Profile Images
- Uses the existing `User.image` field; no additional profile-image table is required.
- `Admin → Site Settings → User Storage Path` is the complete local storage directory.
- Relative storage paths resolve from the Flask application root; absolute paths are used as configured.
- Fresh installs default to `static/images/users`, which resolves to `app/static/images/users`.
- Uploaded JPEG, PNG, or WebP files are decoded/validated, EXIF-oriented, center-cropped to 256x256, metadata-stripped, and re-encoded as WebP with a generated random filename.
- Upload/replace/remove are authenticated, CSRF-protected, rate-limited operations with transaction-aware file cleanup.
- Administrators can immediately remove an unacceptable custom profile image from **Admin → View Users**; the database reference is committed before best-effort file deletion, and users without a custom image do not expose a meaningless removal action.
- Flask-AAS does not publish a generic profile-image/media route. Account/admin pages render the stored generated WebP internally; application plugins decide whether the canonical host profile image is relevant or visible in their own domain. AutoGrid360 now proves this contract by consuming the host image for its public seller identity card without adding duplicate avatar storage/lifecycle behavior.
- Domain media such as AutoGrid360 listing photos remains plugin-owned. S3/object-storage support is a later concern if real host profile-image usage requires it.

### Password Check Providers
Password checking is disabled by default. When enabled in **Admin → Site Settings**, every new-password workflow sends the candidate through the selected provider after the normal local policy checks. The built-in `local` provider uses the packaged common-password blocklist and performs no network requests.

Additional implementations extend `PasswordCheckProvider` and register through `register_password_check_provider()`. The password-setting routes do not need provider-specific code; registered providers become available to the Site Settings provider selector.

### Multi-Factor Authentication
- TOTP enrollment and authenticator replacement
- Fresh reauthentication before MFA setup, replacement, recovery-code rotation, or disable
- Current-TOTP or unused recovery-code confirmation for sensitive MFA changes
- Hashed, display-once, single-use recovery codes with explicit rotation
- Bounded pending-MFA state and lockout handling
- Forced complete login with remember-cookie deletion after terminal MFA failures
- Persistent TOTP-counter replay protection

### Password Reset and Session Revocation
- Cryptographically random password-reset secrets stored only as SHA-256 hashes
- Durable expiry, consumption, and revocation state
- Atomic one-time token consumption with replay rejection
- Revocation of every outstanding reset link after a successful password change
- Authentication-version rotation that invalidates active sessions and remember cookies
- Password-change security notification after the database transaction commits

### Email Verification & Outbound Mail
- Optional email verification using the persisted `activated` account state
- Idempotent verification links with safe handling for malformed, expired, missing-account, and already-used tokens
- Asynchronous mail dispatch so HTTP requests do not wait for SMTP delivery
- Explicit dispatch results: `queued`, `disabled`, or `failed`
- Deployment-managed SMTP with an optional encrypted Site Settings override
- Runtime display of the active mail source: Debug, Site Settings, Environment, Disabled, or Not configured

### Application Plugin Host
- Global **Enable Application Plugins** switch; Flask-AAS operates normally with the plugin host disabled
- Database-backed registration automatically hydrated from `app/plugins/*/plugin.toml` manifests
- Canonical package metadata in `plugin.toml`, inspectable without importing plugin implementation Python
- Explicit per-application enable/disable plus derived configuration and runtime state
- Metadata-only registration: a registered but disabled application does not import its implementation/models, deploy plugin schema, register routes, or contribute navigation during ordinary startup
- Explicit **Enable** trust/code-execution boundary; enabling does **not** silently create or migrate plugin schema
- Independent plugin-owned Alembic histories declared by the manifest, with `plugin_<id>_*` table ownership and `plugin_<id>_alembic_version` version tables
- Fail-closed `NEEDS_MIGRATION`, `NEEDS_CONFIGURATION`, `INCOMPATIBLE`, `ACTIVE`, and `ERROR` runtime states
- Admin **Upgrade Database Schema** action for an enabled compatible plugin that is migration-pending; the browser path upgrades only to `head`
- **Reload App Config** applies structural/runtime-snapshot changes through a fresh Gunicorn worker instead of mutating Flask Blueprints in a running process; already-loaded route/navigation access still follows the current persisted `enabled/configured` gate
- Immediate request/navigation denial after disable, followed by structural removal on reload
- Plugin CLI dispatch through `python manage.py plugin run <plugin_id> ...`; manifest-declared migrations receive host-owned `db` commands automatically while configuration/maintenance commands remain plugin-owned
- Host-owned navigation integration and host theme/template inheritance for plugin pages
- Plugin-owned ordinary configuration and persistence; disabling preserves business data/schema while clearing plugin-managed persisted secrets
- Versioned `PLUGIN_API_VERSION = 1` compatibility boundary
- Route-level authorization remains plugin-owned: plugin routes may be public, require the normal Flask-AAS login context, use existing host roles for coarse admission, or apply plugin-owned domain authorization

Application plugin directories are deployment-supplied native Python code. Filesystem presence automatically registers validated static `plugin.toml` metadata with the application disabled; enabling a plugin is the explicit decision to trust that code to run with the permissions of the Flask-AAS process. Flask-AAS does not claim to sandbox in-process Python plugins.

The human-facing reference implementation is maintained separately in [`flask-aas-example-plugin`](https://github.com/alias454/flask-aas-example-plugin) and may be placed under `app/plugins/example/` like any other trusted deployment-supplied plugin. Flask-AAS keeps only a synthetic plugin fixture under `tests/fixtures/plugin_app/` so host contract tests remain self-contained. The reference plugin demonstrates public/authenticated/admin surfaces, plugin-owned configuration and persistence, independent migrations, navigation, CLI dispatch, and basic admin CRUD without adding a separate application-entitlement subsystem. YATSEE completed the first substantial migration proof, and AutoGrid360 completed the first substantial clean-slate consumer validation of Plugin API v1.

---

### Host Theme & Admin UI
- The selected Flask-AAS theme owns generic typography, forms, buttons, panels, tables, pagination, layout, focus/accessibility behavior, and the main shell.
- Plugin pages inherit the host theme through `templates/plugins/base.html`; plugin CSS is additive and should remain domain-specific.
- The host admin shell intentionally remains separate where it has admin-only behavior, but it shares the same stylesheet and one canonical Admin Menu partial.
- Authenticated destination navigation is sidebar-owned: the top header no longer duplicates **Dashboard** or **Admin** links, while **Logout** remains available as the global session action.
- The Admin homepage provides a compact Overview plus direct User Accounts, Site Settings, and Applications destinations using existing route data only.
- The account page keeps identity/avatar presentation and image controls together, uses a semantic read-only account summary beside Active Sessions, and keeps editable account fields in their own form section.
- Current admin JavaScript is first-party/static and remains compatible with the nonce-based CSP; location lookup also enforces same-origin use.
- HTML response minification preserves explicit document structure and closing tags. The PageGen timing comment is injected after minification while ordinary template comments remain stripped.

### Audit Logging
#### **AuditLogin** (Authentication Attempts)
- Tracks the submitted username or email identifier
- Records IPv4 or IPv6 address, user agent, referrer, and timestamp
- Stores the final authentication outcome rather than password-match status
- Uses normalized internal failure reasons without changing enumeration-resistant public responses
- Remains separate from general application activity auditing

#### **AuditActivity** (User/Admin Actions)
- Tracks actor, action, target, client address, and route-selected metadata
- Stores structured metadata through one portable JSON serialization boundary
- Commits business-success events with the business transaction
- Uses isolated writes for standalone views, denials, failures, and operational tracking
- Supports explicit per-route redaction for token-bearing URL parameters
- Future: filtering, export, retention controls, and analytics

---

### Optional Abuse Detection System
*Modular, pluggable, and fully optional.*

- Blocks brute-force attempts based on:
  - IP address
  - Username
- Threshold example: 10 failures in 5 minutes
- Automatic cooldown resets
- Configurable timers and limits
- Admin/internal service exemptions
- Supports audit logging for lockouts and failed attempts

---

### Security & Rate Limiting Strategy
- Application-level limits use **Flask-Limiter**; an edge proxy or WAF is optional.
- Current route examples:
  - **Login:** `10 / minute`
  - **Registration:** `5 / hour`
  - **Password-reset request:** `10 / hour`
  - **Password-reset submission:** `5 / minute`
  - **CAPTCHA:** `10 / minute` with `50 / 5 minutes` burst control
- Account and administrative routes also use route-specific limits.
- Configure client-IP trust and shared rate-limit storage for the selected deployment topology.

---

### Public Routes & SEO
- `/sitemap.xml` → excludes protected/internal routes and unavailable optional routes
- `/robots.txt` → references sitemap
- The sitemap is generated dynamically so feature-toggle changes take effect immediately

---

## API / Route Endpoints

| Endpoint | Methods | Rule |
|---|---|---|
| about.about | GET | `/about` |
| account.account | GET, POST | `/account` |
| account.remove_profile_image | POST | `/account/profile-image/remove` |
| account.revoke_other_sessions | POST | `/account/sessions/revoke-others` |
| account.revoke_session | POST | `/account/sessions/<int:session_id>/revoke` |
| account.upload_profile_image | POST | `/account/profile-image` |
| admin.admin_home | GET | `/admin/` |
| captcha.captcha_image | GET | `/captcha_image` |
| contact.contact | GET, POST | `/contact` |
| dashboard.dashboard | GET | `/dashboard` |
| favicon.favicon | GET | `/favicon.ico` |
| index.index | GET | `/` |
| locations.zones | GET | `/reference/zones` |
| login.login | GET, POST | `/login` |
| logout.logout | GET | `/logout` |
| mfa.mfa_disable | GET, POST | `/mfa/disable` |
| mfa.mfa_reauth | GET, POST | `/mfa/reauth` |
| mfa.mfa_recovery_codes | GET, POST | `/mfa/recovery-codes` |
| mfa.mfa_replace | GET, POST | `/mfa/replace` |
| mfa.mfa_setup | GET, POST | `/mfa/setup` |
| mfa.mfa_verify | GET, POST | `/mfa/verify` |
| plugins.disable | POST | `/admin/plugins/<int:registration_id>/disable` |
| plugins.enable | POST | `/admin/plugins/<int:registration_id>/enable` |
| plugins.list_plugins | GET | `/admin/plugins/` |
| plugins.reload_config | POST | `/admin/plugins/reload` |
| plugins.run_dataset_action | POST | `/admin/plugins/<int:registration_id>/datasets/<string:dataset_key>/run` |
| plugins.upgrade_schema | POST | `/admin/plugins/<int:registration_id>/upgrade-schema` |
| privacy.privacy | GET | `/privacy` |
| register.register | GET, POST | `/register` |
| reset.change_password | GET, POST | `/change-password` |
| reset.forgot_password | GET, POST | `/forgot-password` |
| reset.reset_password | GET, POST | `/reset-password/<token>` |
| reset.set_password | GET, POST | `/set-password/<token>` |
| robots.robots | GET | `/robots.txt` |
| settings.settings | GET, POST | `/admin/settings/` |
| sitemap.sitemap | GET | `/sitemap.xml` |
| static | GET | `/static/<path:filename>` |
| tos.tos | GET | `/tos` |
| users.delete_user | POST | `/admin/users/<int:user_id>/delete` |
| users.edit_user | GET, POST | `/admin/users/<int:user_id>/edit` |
| users.list_users | GET | `/admin/users/` |
| users.remove_profile_image | POST | `/admin/users/<int:user_id>/profile-image/remove` |
| verify.verify_email_token | GET | `/email/<token>` |

Application-plugin routes are intentionally omitted from this static core-route table because their structural registration depends on the persisted plugin state at worker startup. The host request guard independently denies plugin endpoints that are not effectively usable.

---

## Database Setup & Migrations

Flask-AAS uses **Flask-Migrate** (Alembic) with SQLAlchemy. During the current pre-release phase, generated migration directories are intentionally ignored and are not part of the supported upgrade contract. A clean local or initial deployment may generate its own migration state.

**Initialize a clean development database**

```bash
python manage.py db init
python manage.py db migrate -m "Initial migration"
python manage.py db upgrade
python manage.py seed-db
```

The current pre-release container entrypoint automates this clean-bootstrap sequence when its database-specific initialization marker is absent. The marker name includes a short hash of `SQLALCHEMY_DATABASE_URI`, preventing SQLite and PostgreSQL initialization state from colliding inside one container filesystem. Seed state uses the same pattern. These marker files are not database authority: recreating the web container can rerun bootstrap/seed work against an existing durable database, and `seed-db` is expected to remain safe to repeat.

A completely empty database is detected before startup reads persisted settings. `app/core/schema.py` uses SQLAlchemy schema inspection so early settings and plugin-loader initialization defer cleanly until `env_settings` exists; greenfield PostgreSQL startup should not emit `relation "env_settings" does not exist` errors.

Country and subdivision reference data is vendored under `app/data/` from the `iso-codes` project. `seed-db` synchronizes ISO 3166-1 countries and ISO 3166-2 zones, including subdivision type and parent hierarchy. To refresh the vendored snapshot from a locally installed `iso-codes` package:

```bash
python scripts/update_iso_reference.py
```

The default source directory is `/usr/share/iso-codes/json`; use `--source-dir` when your distribution installs the files elsewhere.

**After local model changes**

```bash
python manage.py db migrate -m "Describe change"
python manage.py db upgrade
```

This is acceptable only while Flask-AAS is pre-release and deployments are treated as clean installs. The host bootstrap boundary is established, but durable in-place **core** release upgrades are not yet claimed.

Application plugins have a separate migration boundary. A plugin manifest may declare a package-local migration environment, for example:

```toml
[plugin]
id = "your_plugin"
name = "Your Plugin"
navigation_label = "Your Plugin"
entrypoint = "app.plugins.your_plugin.plugin:plugin"
migrations = "migrations"
```

For a plugin with declared migrations:

- **Enable** is permission to execute the selected trusted plugin, not permission to mutate its schema;
- an enabled plugin whose schema is behind reports `NEEDS_MIGRATION` and is not structurally registered for normal application use;
- a fresh plugin namespace may create the current plugin-owned model tables and stamp the current migration head;
- an existing versioned plugin runs its own Alembic history;
- existing unversioned `plugin_<id>_*` tables fail closed rather than being blindly stamped;
- plugin history uses its own `plugin_<id>_alembic_version` table and must not own Flask-AAS core tables.

When a manifest declares ``migrations``, Flask-AAS automatically supplies the
standard plugin migration CLI; the plugin does not implement or register these
commands itself. Initializing an Alembic environment and generating a revision
are plugin source-development operations. A published plugin normally ships its
migration environment/history, so an operator ordinarily inspects or upgrades it:

```bash
python manage.py plugin run your_plugin db current
python manage.py plugin run your_plugin db upgrade
```

Plugin authors may additionally use the development commands when creating or
testing schema history:

```bash
python manage.py plugin run your_plugin db init
python manage.py plugin run your_plugin db migrate -m "Describe plugin schema change"
python manage.py plugin run your_plugin db downgrade
```

The top-level ``db`` command is reserved by Flask-AAS for any plugin whose
manifest declares migrations. New plugins gain this lifecycle automatically
from ``plugin.toml`` without adding plugin-specific migration commands to core.

Development migration history remains disposable before the first supported release/checkpoint. Published schema checkpoints become durable upgrade origins. `AAS-039` is complete: manifest-driven plugin migration ownership, independent version tables, migration-aware runtime state, browser/CLI schema management, and core/plugin Alembic ownership isolation are established. Release-grade historical `N -> N+1`, failed-migration, greenfield/history-equivalence, and focused PostgreSQL migration QA remain broader `AAS-031` regression work when real released migration history exists.

---

## Installation

The current tested runtime is **Python 3.13.13**. Generate the lock and run the application with Python 3.13 so environment markers and binary-wheel selection match the deployment image.

```bash
git clone https://github.com/alias454/flask-aas.git
cd flask-aas
python3.13 -m venv .venv
source .venv/bin/activate  # Linux/macOS

python -m pip install --require-hashes -r requirements.txt
cp .env_example .env

export FLASK_APP=app
flask run
```

### Dependency management

`pyproject.toml` is the human-maintained source for direct runtime dependencies. `requirements.txt` is a generated, fully pinned, hash-verified deployment lock and should not be edited manually.

Generate the lock on Fedora 42 Linux x86_64 with Python 3.13.13, pip 26.1.2, and pip-tools 7.6.0. Release validation should install the result in the `python:3.13.13-slim-trixie` container using binary wheels only.

Regenerate the lock from a clean Python 3.13 environment:

```bash
./scripts/lock.sh
```

The lock workflow uses `pip-tools`; deployment still requires only standard `pip` and `requirements.txt`. JWT support uses PyJWT, current password hashing uses Argon2id via `argon2-cffi`, direct `bcrypt` remains only for legacy-hash migration, PostgreSQL uses Psycopg 3, timezone selection uses the standard-library `zoneinfo` API with first-party `tzdata`, and `cryptography` is a direct dependency for encrypted runtime SMTP credentials.

---

### Email Configuration

Outbound email uses an explicit master switch in **Admin → Site Settings**:

```text
Enable Outbound Email
Require Email Verification
```

`Require Email Verification` can be enabled only when outbound email is enabled and an effective mail transport is available. The application does not claim delivery merely because a message was queued. Final SMTP success or failure is logged by the asynchronous worker.

The public contact form is disabled by default. An administrator can enable it
only when Admin Email is configured and outbound email has an effective
transport. If any dependency later becomes unavailable, `/contact` returns 404,
the Contact navigation link is hidden, and the route is omitted from the sitemap.

Contact submissions also pass through the selected spam-check provider when spam
checking is enabled. Fresh installs preserve the existing local protection by
enabling the built-in `local` provider, which reads a packaged phrase list and
performs no network requests. Provider selection is managed in **Admin → Site
Settings** so a later Akismet, ML, or downstream-specific worker can implement the
same pass/fail contract without modifying the contact route. Provider runtime
failures are logged and fail open so an optional spam service cannot take the
contact form offline.

Deployment SMTP settings are supplied through `.env` or another external configuration source:

```dotenv
MAIL_DEBUG=false
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_USERNAME=mailer@example.com
MAIL_PASSWORD=replace-me
MAIL_DEFAULT_SENDER=mailer@example.com
```

Runtime SMTP editing is disabled by default. To permit an administrator-managed override:

```dotenv
MAIL_CONFIG_UI_ENABLED=true
MAIL_CONFIG_ENCRYPTION_KEY=<fernet-key>
```

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Test the active outbound email configuration from the operator CLI. The
configured Admin Email is used by default; `--to` provides a one-time override:

```bash
python manage.py mail-test
python manage.py mail-test --to operator@example.com
```

The command exits unsuccessfully when outbound email is disabled, unavailable,
or cannot be queued.

Effective source precedence is:

1. **Enable Outbound Email** off → delivery disabled.
2. `MAIL_DEBUG=true` → mock delivery; no SMTP connection.
3. Complete Site Settings SMTP configuration → encrypted runtime override.
4. Complete deployment SMTP configuration → environment fallback.
5. No complete source → delivery unavailable.

The two SMTP sources are never blended field by field. A partial Site Settings override is rejected. The saved SMTP password is encrypted with the externally supplied key, is never rendered back into the form, and is cleared only through the explicit override-clear control.

---

### Focused Email Validation

Run the email lifecycle, transport-resolution, encryption, settings-route, and account-state regression suites with:

```bash
python -m pytest \
  tests/test_mailer.py \
  tests/test_mail_config.py \
  tests/test_email_lifecycle.py
```

### Focused Audit and MFA Validation

Run the audit transaction, metadata-redaction, tracking, login-outcome, and MFA lifecycle regression suites with:

```bash
python -m pytest \
  tests/test_audit_tracking.py \
  tests/test_login_audit.py
```

### Focused Password and Spam Provider Validation

Run the password-policy, password-provider, contact-route, and spam-provider regression suites with:

```bash
python -m pytest \
  tests/test_password_policy.py \
  tests/test_password_check.py \
  tests/test_contact.py \
  tests/test_spam_check.py
```

### Focused Plugin Validation

Run the Plugin API/lifecycle/reference-application suites with:

```bash
python -m pytest \
  tests/test_plugin_contract.py \
  tests/test_plugin_manifest.py \
  tests/test_plugin_migrations.py \
  tests/test_plugin_lifecycle.py \
  tests/test_plugin_admin.py \
  tests/test_plugin_web_surface.py \
  tests/test_plugin_integration_surfaces.py \
  tests/test_plugin_bundled.py \
  tests/test_plugin_reload.py \
  tests/test_plugin_fixture_persistence.py
```

Run the complete regression suite with:

```bash
python -m pytest
```

Tests use SQLite by default. Selected audit/account lifecycle suites can also target a disposable PostgreSQL database through the documented test database environment variables so the portable transaction and schema behavior can be exercised on both backends.

### Focused Profile/Theme/Admin Validation

Run the host account-profile, response-minification, theme, and administrative UI contract coverage with:

```bash
python -m pytest \
  tests/test_account_profile.py \
  tests/test_admin_avatar.py \
  tests/test_admin_ui_contract.py \
  tests/test_response_minify.py \
  tests/test_theme_contract.py
```

The most recent confirmed complete host run is:

```text
386 passed, 13 warnings, 22 subtests passed in 27.94s
```


### Build and Run

1. **Build the Docker image (no cache)**

```bash
docker build --pull --no-cache -t flask-aas:local .
```

2. **Run the container**

```bash
docker run -d --env-file .env -p 5000:5000 --name flask-aas flask-aas:local

docker run --rm -it --env-file .env -p 5000:5000 flask-aas:local
```

3. **Access the app**

Open your browser and go to [http://localhost:5000](http://localhost:5000)

### Local Docker Compose database matrix

The base Compose file uses the normal `.env` application configuration, so the default development database remains SQLite:

```bash
docker compose up -d
```

Add the PostgreSQL overlay when explicitly validating the production database backend. The overlay adds the database service and replaces only `SQLALCHEMY_DATABASE_URI` inside the web container; local PostgreSQL values may be supplied through `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT`, otherwise the bounded local defaults are used:

Flask-AAS uses Psycopg 3 and therefore emits `postgresql+psycopg://` URLs. A legacy/generic `postgresql://` value is normalized to the Psycopg 3 dialect during settings validation.

```bash
docker compose -f compose.yml -f compose.postgres.yml up -d
```

Both clean paths are currently validated through core bootstrap, seed, and Gunicorn startup. The PostgreSQL path has also been exercised with the real AutoGrid360 lifecycle:

```text
discover/register disabled
→ administrator Enable
→ Reload App Config
→ NEEDS_MIGRATION
→ Upgrade Database Schema
→ Reload App Config
→ ACTIVE
→ initialize/refresh optional Application Data
```

The same PostgreSQL run successfully exercised the plugin's provisional schema head `6e71dd7952ab` plus automotive/postal dataset actions. This proves the current clean-bootstrap/runtime path; AutoGrid360's first durable packaged migration is still a separate plugin release step.

The repository image uses `python:3.13.13-slim-trixie`, installs the hash-pinned runtime lock with `--require-hashes --only-binary=:all:`, and runs as the unprivileged `flaskaas` account.

These Compose files are development/integration helpers, not a hardened production deployment recipe. Production deployments must supply explicit secrets, site/proxy configuration, durable PostgreSQL/media services, and shared state when multiple workers/instances are claimed.

### Enabling an Application Plugin

The plugin host is disabled by default. After trusted plugin source has been placed under `app/plugins/<id>/` and its manifest has been discovered/registered disabled, the normal activation flow is:

```text
Admin → Site Settings
→ Enable Application Plugins
→ Applications
→ Enable the desired application
→ Reload App Config
→ if NEEDS_MIGRATION: Upgrade Database Schema
→ Reload App Config
→ if NEEDS_CONFIGURATION: complete plugin-owned configuration
→ Reload App Config
→ ACTIVE
```

Flask-AAS does not currently clone, upload, update, or otherwise acquire plugin source; repository placement is an operator/deployment action. `Reload App Config` performs the structural Gunicorn reload and reconciles the worker runtime snapshot with persisted state. For an already structurally loaded plugin, request/navigation gating reads the current persisted `enabled/configured` flags immediately; reload is still the normal administrative step for reconciling runtime status and any startup-time plugin behavior. Database migration remains a separate privileged operation. The explicit plugin CLI is also available for diagnostics, host-provided migration management, configuration, and plugin-owned maintenance when deliberately invoked:

```bash
python manage.py plugin run example status
python manage.py plugin run your_plugin db current
python manage.py plugin run your_plugin db upgrade
python manage.py plugin run example configure
python manage.py plugin run example add-item "example value"
```

The CLI dispatcher does not make the plugin globally active by itself; web runtime activation still follows persisted enablement and the worker reload boundary.

If plugin source was renamed, moved, removed, or its manifest/import path changed and the database registry no longer matches the filesystem, see [`docs/plugin-troubleshooting.md`](docs/plugin-troubleshooting.md) before deleting schema or business data. `PluginRegistration` is persisted state; renaming a directory alone does not rewrite an existing registration row.

---

## Notes
- Seed scripts run once on clean DB
- `default_role_id` in `.env` controls default user role
- Admin panel for user/role/settings/application management
- `User Storage Path` controls the complete local profile-image storage directory; relative paths resolve from the Flask application root and absolute paths are honored as configured.
- Keep deployment SMTP credentials in external configuration, or enable the encrypted Site Settings override deliberately.
- Treat enabled Python application plugins as trusted native application code and keep the Flask-AAS process/container least-privileged.


## Maintenance
### Manual Cleanup
Keep retained operational rows bounded with explicit maintenance commands:

```bash
python manage.py cleanup-logins --days 7
python manage.py cleanup-online-users
```

- **`cleanup-logins --days`** retains login-attempt audit rows for the selected number of days (default: 7).
- **`cleanup-online-users --minutes`** removes stale online-presence rows older than the selected window (default: 10 minutes).
- Online/guest statistics already apply the same active-window cutoff when queried, so stale-row deletion is storage housekeeping and no longer runs synchronously while serving a request.

---

- Run `cleanup-logins` regularly when login-attempt retention is required.
- Run `cleanup-online-users` periodically when visitor tracking is enabled.

- Monitor audit logs for anomalies
- Enable email verification & CAPTCHA for public reg
- Backup DB & user assets