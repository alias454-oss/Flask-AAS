# Flask-AAS

Flask-AAS is a modular Flask authentication, auditing, and application-host foundation. It provides
a reusable security and account layer for applications that need local authentication, MFA, session
management, auditing, administrative controls, mail, abuse controls, and an optional application
plugin system.

Flask-AAS can be used as a standalone authentication/audit base or as the host for trusted
application plugins.

## Background and philosophy

Flask Auth & Audit System began as authentication code written for Open Auto Classifieds. As that
code aged and the surrounding requirements changed, the project was rebuilt in Flask as a reusable
foundation rather than continuing to extend the original application-specific implementation.

The project favors:

- practical features that work locally without mandatory external services;
- explicit security and trust boundaries;
- minimal and understandable dependencies;
- reusable host capabilities instead of application-specific duplication;
- configuration that stays simple for development while supporting stricter deployments;
- application-specific behavior behind a small plugin contract.

## Core features

### Authentication and user management

- Local login with Flask-Login.
- Argon2id password hashing through `argon2-cffi`.
- Transparent verification and login-time upgrade of legacy Flask-Bcrypt hashes.
- Configurable password policy with a passphrase-friendly 20-character default minimum.
- Optional password-check providers, including a built-in local common-password blocklist.
- Registration, account activation, optional administrator approval, and role-based access control.
- Self-service account profile editing.
- Durable active-session tracking and explicit session revocation.
- Sliding authenticated-session inactivity controls.
- Single-user lockdown mode.
- Explicit provisioned-password state: administrator-selected credentials require owner replacement
  after complete authentication/MFA, while development/testing bootstrap avoids repeated clean-instance
  ceremony.
- Global CSRF protection.
- Shared ISO country and subdivision reference data for host and plugin use.

See [`docs/authentication.md`](docs/authentication.md) for authentication, MFA, password reset, and
session behavior.

### Multi-factor authentication

- TOTP enrollment and authenticator replacement.
- Fresh reauthentication before sensitive MFA changes.
- Hashed, display-once, single-use recovery codes.
- Recovery-code rotation.
- Bounded pending MFA state and attempt limits.
- Full-login fallback after terminal MFA failures.
- Persistent TOTP-counter replay protection.

### Password reset and session invalidation

- High-entropy reset secrets stored only as SHA-256 hashes.
- Explicit expiry, consumption, and revocation state.
- Atomic one-time token consumption.
- Revocation of outstanding reset links after password changes.
- Authentication-version rotation that invalidates older sessions and remember cookies.
- Password-change notification after the database transaction commits.

### Audit logging

Flask-AAS keeps authentication-attempt and general activity auditing separate.

**`AuditLogin`**

- Records submitted login identity, client metadata, timestamp, final outcome, and normalized failure
  reason.
- Records the final authentication outcome rather than exposing password-match details.
- Uses an isolated write path appropriate for authentication attempts.

**`AuditActivity`**

- Records actor, action, target, trusted client address, and route-selected metadata.
- Participates in the caller-owned transaction for business-success events.
- Uses isolated writes for standalone events, denials, failures, and operational tracking.
- Supports explicit redaction for token-bearing route parameters.

Authoritative audit metadata should contain stable, bounded information rather than raw credentials,
tokens, form submissions, or raw database exception strings.

### Email and verification

- Optional email verification using the persisted account activation state.
- Asynchronous mail dispatch so HTTP requests do not wait for SMTP delivery.
- Explicit `queued`, `disabled`, and `failed` dispatch outcomes.
- Deployment-managed SMTP configuration.
- Optional encrypted administrator-managed SMTP override.
- Debug/mock delivery without an SMTP connection.
- Contact-form availability tied to effective mail configuration.

See [`docs/email.md`](docs/email.md).

### Abuse controls

- Flask-Limiter route-level rate limits.
- Login protection keyed by both account identity and client address where appropriate.
- Configurable lockout/cooldown behavior.
- Optional CAPTCHA.
- Optional spam-check providers for the contact workflow.
- Explicit proxy/client-IP trust configuration.

A single-process development server may use in-memory enforcement state. Multiple workers or
instances need shared state where enforcement must remain consistent.

### Profile images

The host owns the canonical user profile image lifecycle:

- JPEG, PNG, and WebP input.
- Decoded-content validation.
- EXIF orientation handling.
- Center-crop normalization to WebP.
- Generated filenames and metadata stripping.
- Authenticated, CSRF-protected, rate-limited upload/replace/remove actions.
- Administrator image takedown.
- Transaction-aware cleanup of superseded files.

Fresh installations default to `uploads/users`.

See [`docs/media-storage.md`](docs/media-storage.md).

### Application plugin host

Application hosting is opt-in. Flask-AAS remains usable as an authentication/audit base when the
plugin system is disabled.

Plugin API v1 provides:

- metadata-only discovery from `app/plugins/*/plugin.toml`;
- disabled-by-default registration;
- explicit administrator enable/disable;
- a native-code trust boundary at enablement;
- independent plugin-owned Alembic migration histories;
- `NEEDS_MIGRATION`, `NEEDS_CONFIGURATION`, `ACTIVE`, `INCOMPATIBLE`, and `ERROR` states;
- host-managed plugin migration commands;
- host navigation and theme integration;
- plugin-owned configuration and persistence;
- immediate request denial after disable;
- structural activation/removal through a fresh Gunicorn worker;
- generic plugin CLI dispatch.

Enabled Python plugins execute with Flask-AAS process privileges. Flask-AAS does not claim to
sandbox them.

See [`docs/plugins.md`](docs/plugins.md) and
[`docs/plugin-troubleshooting.md`](docs/plugin-troubleshooting.md).

## Installation

The currently validated runtime is Python 3.13.13.

```bash
git clone https://github.com/alias454-oss/Flask-AAS.git
cd flask-aas

python3.13 -m venv .venv
source .venv/bin/activate

python -m pip install --require-hashes -r requirements.txt
cp .env_example .env

export FLASK_APP=app
flask run
```

`pyproject.toml` is the human-maintained source for direct dependencies. `requirements.txt` is the
generated, fully pinned, hash-verified deployment lock and should not be edited manually.

Regenerate the lock from a clean Python 3.13 environment with:

```bash
./scripts/lock.sh
```

## Database setup and migrations

Flask-AAS uses SQLAlchemy and Flask-Migrate/Alembic and ships its migration history.

For a clean local database:

```bash
python manage.py db upgrade
python manage.py seed-db
```

Do not run `db init` or generate a replacement initial migration for a normal checkout.

After a model change during development:

```bash
python manage.py db migrate -m "Describe change"
python manage.py db upgrade
```

Migration history is released as **rolled-up checkpoints**. A released/supported checkpoint is a
durable upgrade origin. Development-only revisions after the latest released checkpoint may be
consolidated before the next release so the permanent history records the net schema change between
supported checkpoints instead of every intermediate edit.

Before consolidating unpublished revisions, back up the development database and migration tree,
regenerate the rolled-up migration, re-identify/stamp the known-equivalent development database at the
new head, run the regression suite, and remove the backups only after validation succeeds.

Application plugins have a separate migration boundary. Plugins that declare migrations receive
host-managed commands such as:

```bash
python manage.py plugin run <plugin_id> db current
python manage.py plugin run <plugin_id> db upgrade
```

Plugin authors may additionally use `db init`, `db migrate`, and `db downgrade` while developing
their own schema history. Released plugin migration checkpoints are durable; unpublished revisions
after the latest released checkpoint may be rolled up before the next release.

## Bootstrap administrator

`python manage.py seed-db` creates the bootstrap `admin` only when that account does not already
exist. `ADMIN_SECRET` is therefore a bootstrap credential, not a recurring password-reset mechanism.

In production, a newly seeded administrator is marked `must_change_password=True`. Normal password
authentication and any required MFA complete first, then the administrator must choose a private
replacement password before normal authenticated navigation continues.

Development and testing deliberately skip the forced-change ceremony for the freshly seeded bootstrap
administrator so disposable clean instances remain low-friction. This exception does not apply to
ordinary administrator-created users: an administrator-selected password still requires replacement
in every environment.

Reseeding or restarting an existing database does not reset the administrator password or
forced-change state.

## Docker

Build and run the application image:

```bash
docker build --pull --no-cache -t flask-aas:local .

docker run --rm -it \
  --env-file .env \
  -p 5000:5000 \
  flask-aas:local
```

### Docker Compose

The base Compose path uses the normal application configuration and supports the default SQLite
development workflow:

```bash
docker compose up
```

Use the PostgreSQL overlay when validating PostgreSQL behavior:

```bash
docker compose \
  -f compose.yml \
  -f compose.postgres.yml \
  up
```

Both paths support clean bootstrap. The PostgreSQL path has also been exercised through plugin
discovery, enablement, migration, reload, activation, and plugin dataset initialization.

The Compose files are development/integration helpers rather than a hardened production recipe.

## Production deployment

A public deployment should provide:

- a stable externally supplied `SECRET_KEY`;
- an explicit public `SITE_URL`;
- HTTPS at the external boundary;
- proxy trust that matches the actual topology;
- durable PostgreSQL storage;
- durable profile/plugin media storage;
- shared rate-limit/cache state when multiple workers or instances are used;
- outbound mail configuration when mail-dependent features are enabled;
- database and media backups.

Direct local development does not require PostgreSQL, Redis, SMTP, internal TLS, or a manually
supplied secret.

See [`docs/deployment-modes.md`](docs/deployment-modes.md) for the detailed deployment contract.

## Email configuration

Outbound mail is controlled by **Admin → Site Settings → Enable Outbound Email**.

Deployment SMTP can be configured externally:

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

To permit an encrypted Site Settings SMTP override:

```dotenv
MAIL_CONFIG_UI_ENABLED=true
MAIL_CONFIG_ENCRYPTION_KEY=<fernet-key>
```

Test the effective configuration with:

```bash
python manage.py mail-test
python manage.py mail-test --to operator@example.com
```

See [`docs/email.md`](docs/email.md) for precedence, verification dependencies, and runtime behavior.

## Enabling an application plugin

Trusted plugin source is placed under:

```text
app/plugins/<plugin_id>/
```

Normal activation is:

```text
Admin → Site Settings
→ Enable Application Plugins
→ Applications
→ Enable plugin
→ Reload App Config
→ if NEEDS_MIGRATION: Upgrade Database Schema
→ Reload App Config
→ if NEEDS_CONFIGURATION: complete plugin-owned configuration
→ Reload App Config
→ ACTIVE
```

Flask-AAS does not clone, upload, or install plugin source. Plugin acquisition remains an
operator/deployment action.

The CLI is available for diagnostics, migration management, configuration, and plugin-owned
maintenance:

```bash
python manage.py plugin run <plugin_id> status
python manage.py plugin run <plugin_id> db current
python manage.py plugin run <plugin_id> db upgrade
```

## Core routes

The table below lists Flask-AAS host/core routes. Application-plugin routes are intentionally omitted
because their structural registration depends on plugin state at worker startup.

| Endpoint | Methods | Rule |
|---|---|---|
| `about.about` | GET | `/about` |
| `account.account` | GET, POST | `/account` |
| `account.remove_profile_image` | POST | `/account/profile-image/remove` |
| `account.revoke_other_sessions` | POST | `/account/sessions/revoke-others` |
| `account.revoke_session` | POST | `/account/sessions/<int:session_id>/revoke` |
| `account.upload_profile_image` | POST | `/account/profile-image` |
| `admin.admin_home` | GET | `/admin/` |
| `captcha.captcha_image` | GET | `/captcha_image` |
| `contact.contact` | GET, POST | `/contact` |
| `dashboard.dashboard` | GET | `/dashboard` |
| `favicon.favicon` | GET | `/favicon.ico` |
| `index.index` | GET | `/` |
| `locations.zones` | GET | `/reference/zones` |
| `login.login` | GET, POST | `/login` |
| `logout.logout` | GET | `/logout` |
| `mfa.mfa_disable` | GET, POST | `/mfa/disable` |
| `mfa.mfa_reauth` | GET, POST | `/mfa/reauth` |
| `mfa.mfa_recovery_codes` | GET, POST | `/mfa/recovery-codes` |
| `mfa.mfa_replace` | GET, POST | `/mfa/replace` |
| `mfa.mfa_setup` | GET, POST | `/mfa/setup` |
| `mfa.mfa_verify` | GET, POST | `/mfa/verify` |
| `plugins.disable` | POST | `/admin/plugins/<int:registration_id>/disable` |
| `plugins.enable` | POST | `/admin/plugins/<int:registration_id>/enable` |
| `plugins.list_plugins` | GET | `/admin/plugins/` |
| `plugins.reload_config` | POST | `/admin/plugins/reload` |
| `plugins.run_dataset_action` | POST | `/admin/plugins/<int:registration_id>/datasets/<string:dataset_key>/run` |
| `plugins.upgrade_schema` | POST | `/admin/plugins/<int:registration_id>/upgrade-schema` |
| `privacy.privacy` | GET | `/privacy` |
| `register.register` | GET, POST | `/register` |
| `reset.change_password` | GET, POST | `/change-password` |
| `reset.forgot_password` | GET, POST | `/forgot-password` |
| `reset.reset_password` | GET, POST | `/reset-password/<token>` |
| `reset.set_password` | GET, POST | `/set-password/<token>` |
| `robots.robots` | GET | `/robots.txt` |
| `settings.settings` | GET, POST | `/admin/settings/` |
| `sitemap.sitemap` | GET | `/sitemap.xml` |
| `static` | GET | `/static/<path:filename>` |
| `tos.tos` | GET | `/tos` |
| `users.delete_user` | POST | `/admin/users/<int:user_id>/delete` |
| `users.edit_user` | GET, POST | `/admin/users/<int:user_id>/edit` |
| `users.list_users` | GET | `/admin/users/` |
| `users.remove_profile_image` | POST | `/admin/users/<int:user_id>/profile-image/remove` |
| `verify.verify_email_token` | GET | `/email/<token>` |

## Development and testing

Run the complete regression suite with:

```bash
python -m pytest
```

Useful focused suites include:

```bash
python -m pytest tests/test_login_audit.py tests/test_audit_tracking.py

python -m pytest \
  tests/test_email_lifecycle.py \
  tests/test_mailer.py \
  tests/test_mail_config.py

python -m pytest \
  tests/test_plugin_contract.py \
  tests/test_plugin_lifecycle.py \
  tests/test_plugin_migrations.py

python -m pytest \
  tests/test_account_profile.py \
  tests/test_admin_ui_contract.py \
  tests/test_theme_contract.py
```

Changes involving SQL semantics, migration behavior, or deployment boundaries should also be
exercised against PostgreSQL. SQLite can permit SQL behavior that PostgreSQL rejects.

## Operator maintenance

Common host maintenance commands include:

```bash
python manage.py cleanup-logins --days 7
python manage.py cleanup-online-users
python manage.py seed-db
```

Run cleanup commands on a deployment-appropriate schedule when the corresponding retained state is
enabled.

## Public routes and SEO

Flask-AAS provides:

- `/robots.txt`
- `/sitemap.xml`

The sitemap excludes protected/internal routes and unavailable optional routes and is generated
dynamically so feature-state changes are reflected.

## Security model

Important project boundaries include:

- forwarding headers are ignored unless proxy trust is explicitly configured;
- host validation and client-IP trust are separate controls;
- sensitive state changes use CSRF protection and stronger reauthentication where required;
- token-bearing routes define audit redaction;
- audit helpers do not silently own caller business transactions;
- passwords, tokens, cookies, SMTP credentials, and plugin-managed secrets are not intended for logs;
- disabled plugins remain inert during ordinary startup;
- plugin enablement and plugin schema migration are separate privileged operations;
- provisioned administrator credentials cannot bypass their required owner-selected replacement;
- released migration checkpoints remain stable upgrade origins while unpublished development churn may
  be rolled up before the next checkpoint;
- process-local security state is not treated as authoritative across multiple workers/instances.

See:

- [`docs/security-checklist.md`](docs/security-checklist.md)
- [`docs/security-tooling.md`](docs/security-tooling.md)

## Documentation

- [`docs/authentication.md`](docs/authentication.md) — authentication, MFA, reset, and sessions
- [`docs/deployment-modes.md`](docs/deployment-modes.md) — development and production behavior
- [`docs/email.md`](docs/email.md) — outbound email and verification behavior
- [`docs/media-storage.md`](docs/media-storage.md) — host media storage contract
- [`docs/plugins.md`](docs/plugins.md) — Plugin API v1 lifecycle and trust model
- [`docs/plugin-troubleshooting.md`](docs/plugin-troubleshooting.md) — plugin recovery
- [`docs/security-checklist.md`](docs/security-checklist.md) — route/plugin security review
- [`docs/security-tooling.md`](docs/security-tooling.md) — tooling and CI baseline

## License

See [`LICENSE`](LICENSE).