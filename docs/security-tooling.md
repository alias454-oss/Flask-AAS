# Security Tooling Baseline

Security tooling should support the review process, not replace code-level reasoning or tests.


## Current dependency checkpoint

The 2026-08-03 dependency/runtime checkpoint uses:

- Python 3.13.13;
- pip 26.1.2 and pip-tools 7.6.0 for lock generation;
- `pyproject.toml` for direct dependencies;
- `requirements.txt` as the hash-pinned deployment lock;
- PyJWT for JWT support;
- Flask-Bcrypt as the single password-hashing implementation;
- `python:3.13.13-slim-trixie` as the validated container base.

The lock was generated on Fedora 42 Linux x86_64 and validated in the Trixie container with `--require-hashes` and `--only-binary=:all:`. Dependency auditing should continue to run against the generated lock before release checkpoints.

## Current audit-integrity checkpoint

The 2026-08-03 audit/tracking checkpoint established these invariants:

- `AuditActivity` business-success events participate in the caller-owned transaction;
- login attempts, standalone activity events, and online-presence updates use isolated writes;
- `AuditActivity.extra_data` is encoded exactly once and legacy double-encoded rows remain readable;
- token-bearing routes declare explicit parameter redaction without reducing normal route audit detail;
- `AuditLogin` records one final outcome per attempt with a normalized failure reason;
- direct requests ignore spoofed forwarded headers unless proxy trust is explicitly configured.

Focused regression coverage is maintained in `tests.test_audit_tracking` and `tests.test_login_audit`.

## Current email-lifecycle checkpoint

The 2026-08-04 email checkpoint established these invariants:

- verification uses the registered endpoint names and the persisted `User.activated` state;
- valid verification is idempotent, malformed or expired tokens fail safely, and activation commits with its audit event;
- template rendering resolves the packaged HTML and text template trees;
- callers receive `queued`, `disabled`, or `failed` dispatch status without waiting for SMTP;
- **Enable Outbound Email** is the application master switch and required verification depends on an available transport;
- complete Site Settings SMTP configuration may override deployment configuration only when `MAIL_CONFIG_UI_ENABLED=true`;
- partial runtime overrides are rejected rather than blended with environment values;
- UI-managed SMTP passwords are Fernet-encrypted with an external key, are not rendered back into forms, and are excluded from audit metadata;
- the background worker receives an immutable resolved configuration rather than mutating global application configuration.

Focused regression coverage is maintained in `tests.test_mailer`, `tests.test_mail_config`, and `tests.test_email_lifecycle`. The combined email, login, and audit checkpoint runs 69 tests.

## Current MFA checkpoint

The 2026-08-05 MFA checkpoint established these invariants:

- MFA enrollment, authenticator replacement, and disable require fresh authentication;
- remembered or otherwise non-fresh sessions must complete MFA reauthentication before changing MFA state;
- MFA reauthentication and disable accept either the current TOTP or an unused single-use recovery code;
- transient setup, verification, reauthentication, and disable state is bounded by timestamps and attempt limits;
- terminal MFA lockout forces a complete login and deletes the remember cookie;
- the accepted TOTP counter is persisted and an already accepted code is rejected;
- final MFA-login persistence failure does not leave an accepted login session;
- replay regression tests isolate MFA matching behavior without replacing the process-wide clock.

Focused MFA coverage is maintained in `tests.test_login_audit`.

## Current password-reset checkpoint

The 2026-08-05 password-reset checkpoint established these invariants:

- reset secrets are generated with high entropy and stored only as SHA-256 hashes;
- reset records carry explicit expiry, consumption, and revocation state;
- token consumption is a conditional database update, so only one concurrent request can succeed;
- requesting another reset link does not revoke an earlier valid link through an unauthenticated denial-of-service path;
- a successful reset consumes its token and revokes every other outstanding reset token for the account;
- authenticated password changes also revoke all outstanding reset tokens;
- every password change rotates the user authentication version, invalidating active sessions and remember cookies across clients;
- the current browser is forced through a complete login and its remember cookie is deleted;
- password-change notification is queued only after the password transaction commits;
- a failed database commit preserves the old password, authentication version, and token usability.

Focused password-reset coverage is maintained in `tests.test_email_lifecycle` and `tests.test_login_audit`. `ACCOUNT_TEST_DATABASE_URI` may point those lifecycle tests at a disposable PostgreSQL database.

## Current inactivity checkpoint

The 2026-08-05 inactivity checkpoint established these invariants:

- authenticated browser sessions use a numeric, sliding inactivity timestamp rather than a nonexistent custom user-session key;
- the timeout is configurable through `SESSION_INACTIVITY_TIMEOUT_SECONDS`, with `0` disabling it explicitly;
- the exact timeout boundary forces a complete login and deletes the remember cookie;
- successful primary and MFA logins seed the inactivity window immediately;
- remembered-session restoration starts a new non-fresh inactivity window;
- missing, malformed, legacy, or future activity timestamps recover into a new bounded window rather than creating an indefinite session;
- static asset requests do not refresh authenticated activity;
- pre-authentication MFA state remains governed by its separate expiry and attempt limits;
- inactivity expiry clears authenticated, transient MFA, and other browser-session state.

Focused inactivity coverage is maintained in `tests/test_inactivity.py`, with login integration assertions in `tests/test_login_audit.py`.

## Current profile-image checkpoint

The 2026-08-12 host profile-image checkpoint establishes these invariants:

- the existing `User.image` field stores only a generated WebP basename; no profile-image schema migration or generic media-serving route is introduced;
- `EnvSettings.users_stored_path` is the complete administrator-selected local storage directory;
- JPEG, PNG, and WebP uploads are decoded/validated, bounded, EXIF-oriented, metadata-stripped, center-cropped, and normalized before persistence;
- owner upload/replace/remove and administrator takedown are authenticated, CSRF-protected, rate-limited state changes;
- replacement/removal makes the database decision durable before deleting the superseded generated file, while commit failure preserves the prior reference/file;
- the account page renders the canonical image internally and keeps image controls with the identity presentation; the admin user list exposes removal only for users with a custom image.

Focused coverage is maintained in `tests/test_account_profile.py`, `tests/test_admin_avatar.py`, `tests/test_admin_ui_contract.py`, and `tests/test_theme_contract.py`. The current complete Flask-AAS suite is **369 passed, 11 warnings, and 22 subtests passed**.

## Current application-plugin checkpoint

The 2026-08-08 application-plugin checkpoint establishes these invariants:

- the global plugin-system switch may remain disabled without loading application-plugin runtime code;
- bundled plugin registration is explicit metadata, not writable-directory discovery;
- canonical static plugin identity/compatibility metadata comes from `plugin.toml` and can be validated without importing implementation Python;
- registration alone does not import plugin implementation or model modules and does not deploy plugin schema;
- explicit administrator enablement is the selected plugin's native-code trust boundary, not a schema-migration operation;
- plugins with declared migrations use independent `plugin_<id>_*` namespaces and `plugin_<id>_alembic_version` histories;
- an enabled plugin with stale schema fails closed as `NEEDS_MIGRATION` before structural application registration;
- fresh plugin namespaces may bootstrap the current owned model schema and stamp head, while existing unversioned owned tables fail closed;
- the admin schema-upgrade path is POST/admin/CSRF/rate-limit protected, upgrades only to `head`, and does not expose browser downgrade/arbitrary revision execution;
- structural runtime activation occurs only when a fresh Gunicorn worker starts;
- persisted schema/configuration changes are distinct from the running worker's status snapshot; already-loaded route/navigation gates follow current persisted `enabled/configured` state immediately, while **Reload App Config** reconciles startup-time runtime status and structure;
- disabled plugins do not contribute routes or navigation after reload, while the request guard denies a newly disabled plugin immediately;
- enabled-but-unconfigured plugins remain inaccessible until their plugin-owned validation succeeds;
- plugin configuration and persistence remain plugin-owned rather than moving domain settings into Flask-AAS core;
- disabling a plugin preserves ordinary configuration, business data, and schema while clearing plugin-managed persisted secrets;
- generic plugin CLI dispatch logs plugin and command identity without logging arbitrary command arguments;
- plugin pages inherit the host template/theme baseline and add only plugin-local presentation overrides;
- enabled Python plugins execute with Flask-AAS process privileges and are not treated as sandboxed code.

Focused plugin coverage is maintained in `tests/test_plugin_contract.py`, `tests/test_plugin_manifest.py`, `tests/test_plugin_migrations.py`, `tests/test_plugin_lifecycle.py`, `tests/test_plugin_admin.py`, `tests/test_plugin_web_surface.py`, `tests/test_plugin_integration_surfaces.py`, `tests/test_plugin_bundled.py`, `tests/test_plugin_reload.py`, and `tests/test_plugin_example_persistence.py`. The current full suite passes **239 tests, 11 warnings, and 15 subtests**.

## Recommended baseline

### Ruff

Use for fast Python linting and import/style checks. Keep the rule set small enough that findings remain actionable.

### Bandit

Use for basic Python security-pattern detection. Treat findings as review leads rather than proof of a vulnerability.

### Semgrep

Use for framework-aware and project-specific checks. Add local rules for Flask-AAS invariants such as:

- token-bearing routes or audit calls without explicit parameter redaction;
- audit helpers calling `commit()` or `rollback()` on the caller-owned session;
- state-changing GET routes;
- CSRF-exempt browser routes;
- security-sensitive routes without fresh reauthentication;
- request metadata capture that fails to exclude authorization or cookie headers;
- `ProxyFix` enabled without explicit configuration;
- normal application startup importing disabled plugin implementation/model modules;
- filesystem scanning or implicit import of undeclared plugin packages;
- plugin operational logging that records arbitrary CLI arguments.

### Dependency audit

Audit pinned runtime dependencies in CI and before releases. A dependency finding should include:

- affected package and resolved version;
- whether the vulnerable code path is used;
- available fixed version;
- compatibility impact;
- remediation or accepted-risk decision.

### CodeQL or equivalent repository scanning

Enable repository-level scanning for data-flow and injection classes that local linting may miss.

### Pytest

The security regression suite is the most important control in this list. Static tools cannot prove authentication state, transaction behavior, token replay resistance, or environment-mode behavior.

## Suggested CI stages

1. Dependency installation from the canonical lock or requirement set
2. Linting
3. Unit and integration tests
4. Database migration tests
5. Dependency audit
6. Python SAST
7. Repository data-flow scanning
8. Container build and configuration smoke test

## Project-specific checks to automate

- Every submitted login produces exactly one final `AuditLogin` outcome.
- Successful login rows are written only after Flask-Login accepts the user.
- Sensitive MFA state changes require fresh authentication.
- Forced full-login paths delete remembered authentication state.
- An accepted TOTP counter cannot be replayed.
- Password-reset secrets are hashed at rest and accepted only once.
- Password changes revoke outstanding reset links and invalidate earlier session identities.
- Token-bearing routes declare redaction and no concrete token appears anywhere in the stored audit row.
- No concrete reset or verification token appears in audit rows.
- Audit helpers do not call transaction-ending methods.
- Direct-development mode starts without SMTP, Redis, certificates, or a supplied secret.
- Production mode rejects missing stable secrets.
- Proxy trust is disabled unless configured.
- HSTS and secure cookies are not forced during direct HTTP development.
- All registered Flask-AAS core routes resolve their endpoint references; runtime application-plugin routes are validated under their own lifecycle tests.
- All email templates referenced by code exist.
- Required verification cannot be enabled without an effective outbound transport.
- Runtime SMTP overrides are all-or-nothing, encrypted, and ignored when UI configuration is disabled.
- SMTP credentials never appear in rendered forms, logs, or audit metadata.
- Queued email is not represented as completed SMTP delivery.
- Migrations upgrade a new database and a representative prior schema.
- A globally disabled plugin system leaves the Flask-AAS core usable and does not load plugin runtime code.
- Registered-but-disabled plugins do not import implementation/model modules, deploy schema, register routes, or contribute navigation during ordinary startup.
- Plugin manifests and migration declarations are validated without importing plugin implementation code.
- Core Alembic autogeneration preserves the core-owned `plugin_registrations` table while never adopting plugin-domain namespaces such as `plugin_example_*` that belong to plugin migration histories.
- Plugin migration failure never falsely advances `plugin_<id>_alembic_version`.
- Persisted plugin configuration drift is tested explicitly: request/navigation gating follows current persisted state on already-loaded surfaces, while worker runtime status remains stale until Reload App Config.
- Explicit plugin enablement is a trusted code-execution boundary but does not migrate schema; structural runtime activation requires a fresh worker.
- Disabling a plugin immediately denies access, clears plugin-managed persisted secrets, preserves ordinary data/schema/configuration, and removes runtime registration after reload.
- Plugin CLI logging never exposes arbitrary command arguments or managed secret values.
- An incompatible or failed optional plugin does not prevent core startup.
- CSP tests cover interactive controls without allowing inline script.

## Larger platforms

SonarQube, commercial SAST, or broader application-security platforms may be useful later, but they are not prerequisites for this project. Add them only when they provide a concrete benefit beyond the baseline and do not create an unmaintained findings queue.
