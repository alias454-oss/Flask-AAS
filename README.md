# Flask-AAS

Work In Progress — usable for development, testing, and controlled internal evaluation.

> **Pre-release status:** Flask-AAS is under active development. Review the documented configuration and deployment guidance before use.

Flask-AAS is a  **modular Flask-based authentication and auditing system** with built-in user management, log tracking,
and optional abuse prevention features. Designed for small projects but scalable for larger apps that require **robust security tooling**.

---

## Background & Philosophy
The Flask Auth & Audit System began life as a simple PHP login script written a long time ago as a foundational part of
Open Auto Classifieds. Over time, it evolved into a full-featured authentication, user management, and audit logging platform.

While the original worked well, until it didn't. The need for a more modern, secure, and flexible solution led to a
complete rebuild in Flask. The result is a modular foundation that can be used as a starting point on my other projects.

This project focuses on:
- Keeping external dependencies minimal
- Providing practical features that work out of the box
- Leaving optional integrations and extras up to you
- Staying adaptable for both small projects and larger ones

## Project Documentation

| Document | Purpose |
|---|---|
| [`docs/deployment-modes.md`](docs/deployment-modes.md) | Development versus deployed behavior |
| [`docs/security-checklist.md`](docs/security-checklist.md) | Reusable route-review checklist |
| [`docs/security-tooling.md`](docs/security-tooling.md) | Static analysis, dependency audit, and CI baseline |

The base is intentionally designed to remain easy to run locally. Direct HTTP, generated development secrets, SQLite, and in-memory services are valid development choices. Stricter requirements apply only when the selected deployment mode needs them.

## Core Features

### Authentication & User Management
- Secure login with **Flask-Login**
- Password hashing via **bcrypt**
- Active session tracking
- Sliding authenticated-session inactivity timeout with remember-cookie deletion on expiry
- Remember-cookie-restored sessions begin a new non-fresh inactivity window
- Account state flags:
  - `activated` → Email verification status
  - `approved` → Optional admin review
- Role-based access control (RBAC)
- Flexible registration fields (company, phone, location, etc.)
- Admin panel with settings management
- Single-user lockdown mode
- Global CSRF protection

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

---

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
- `/sitemap.xml` → excludes protected/internal routes
- `/robots.txt` → references sitemap
- Both cached for efficiency

---

## API / Route Endpoints

| Endpoint | Methods | Rule |
|----------|---------|------|
| about.about | GET | `/about` |
| admin.admin_home | GET | `/admin/` |
| captcha.captcha_image | GET | `/captcha_image` |
| contact.contact | GET, POST | `/contact` |
| dashboard.dashboard | GET | `/dashboard` |
| favicon.favicon | GET | `/favicon.ico` |
| index.index | GET | `/` |
| login.login | GET, POST | `/login` |
| logout.logout | GET | `/logout` |
| mfa.mfa_disable | GET, POST | `/mfa/disable` |
| mfa.mfa_setup | GET, POST | `/mfa/setup` |
| mfa.mfa_verify | GET, POST | `/mfa/verify` |
| privacy.privacy | GET | `/privacy` |
| register.register | GET, POST | `/register` |
| reset.change_password | GET, POST | `/change-password` |
| reset.forgot_password | GET, POST | `/forgot-password` |
| reset.reset_password | GET, POST | `/reset-password/<token>` |
| robots.robots | GET | `/robots.txt` |
| settings.settings | GET, POST | `/admin/settings/` |
| sitemap.sitemap | GET | `/sitemap.xml` |
| static | GET | `/static/<path:filename>` |
| tos.tos | GET | `/tos` |
| users.delete_user | POST | `/admin/users/<int:user_id>/delete` |
| users.edit_user | GET, POST | `/admin/users/<int:user_id>/edit` |
| users.list_users | GET | `/admin/users/` |
| verify.verify_email_token | GET | `/email/<token>` |
| verify.verify_reset_token | GET | `/reset/<token>` |

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

**After local model changes**

```bash
python manage.py db migrate -m "Describe change"
python manage.py db upgrade
```

This is acceptable only while Flask-AAS is pre-release and deployments are treated as clean installs. Durable in-place upgrades require a future versioned migration policy; that work remains tracked as `AAS-021` / `SR-019`.

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

The current lock baseline was generated on Fedora 42 Linux x86_64 with Python 3.13.13, pip 26.1.2, and pip-tools 7.6.0. It was validated in the `python:3.13.13-slim-trixie` container using binary wheels only.

Regenerate the lock from a clean Python 3.13 environment:

```bash
./scripts/lock.sh
```

The lock workflow uses `pip-tools`; deployment still requires only standard `pip` and `requirements.txt`. JWT support uses PyJWT, password hashing and verification use the single Flask-Bcrypt stack, and `cryptography` is a direct dependency for encrypted runtime SMTP credentials.

---

### Email Configuration

Outbound email uses an explicit master switch in **Admin → Site Settings**:

```text
Enable Outbound Email
Require Email Verification
```

`Require Email Verification` can be enabled only when outbound email is enabled and an effective mail transport is available. The application does not claim delivery merely because a message was queued. Final SMTP success or failure is logged by the asynchronous worker.

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
python -m unittest -v \
  tests.test_mailer \
  tests.test_mail_config \
  tests.test_email_lifecycle
```

### Focused Audit and MFA Validation

Run the audit transaction, metadata-redaction, tracking, login-outcome, and MFA lifecycle regression suites with:

```bash
python -m unittest discover \
  -s tests \
  -p 'test_login_audit.py' \
  -v
```

Run the complete regression suite with:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

The current suite contains 104 tests. Tests use SQLite by default. Set `AUDIT_TEST_DATABASE_URI` to a disposable PostgreSQL database URI for the audit suite, or `ACCOUNT_TEST_DATABASE_URI` for the account and password-reset lifecycle suite, to exercise the same portable behavior against PostgreSQL.

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

---

## Notes
- Seed scripts run once on clean DB
- `default_role_id` in `.env` controls default user role
- Admin panel for user/role/settings management
- Keep deployment SMTP credentials in external configuration, or enable the encrypted Site Settings override deliberately.


## Maintenance
### Manual Log Cleanup
Keep log tables lean with the CLI cleanup command:

```bash
python manage.py cleanup-logins --days 7
```
- **`--days`** → Number of days to retain logs (default: 7)
- Deletes `AuditLogin` records older than the retention period

---

- Run `cleanup-logins` regularly when login-attempt retention is required

- Monitor audit logs for anomalies
- Enable email verification & CAPTCHA for public reg
- Backup DB & user assets
