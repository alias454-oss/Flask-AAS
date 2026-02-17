# Flask-AAS

Work In Progress — usable for testing and internal use.

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

## Core Features

### Authentication & User Management
- Secure login with **Flask-Login**
- Password hashing via **bcrypt**
- Active session tracking
- Account state flags:
  - `activated` → Email verification status
  - `approved` → Optional admin review
- Role-based access control (RBAC)
- Flexible registration fields (company, phone, location, etc.)
- Admin panel with settings management
- Single-user lockdown mode
- Global CSRF protection

---

### Audit Logging
#### **AuditLogin** (Login Attempts)
- Tracks username/email used
- Records IP address (stored as integer)
- Logs timestamp, success/failure, and failure reason

#### **AuditActivity** (User/Admin Actions)
- Tracks key actions such as settings changes or account modifications
- Stores payload as JSON for flexibility
- Supports actor/target tracking
- Future: filtering, export, and analytics

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
- All lockouts and failed attempts are audit-logged

---

### Security & Rate Limiting Strategy
- **Multi-layer**: Cloudflare WAF (edge) + Flask-Limiter (app)
- Rate limits by route:
  - **Login:** `5 / 5min` per IP+username
  - **Password reset:** `10 / min` per IP
  - **CAPTCHA:** `10 / min` (burst to `50 / 5min`)
- Admin/dashboard generally exempt but monitored
- Configurable via env/config
- IP whitelisting support

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
| dashboard.dashboard | GET | `/dashboard` |
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
| reset.test_email | GET | `/test-email` |
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
Uses **Flask-Migrate** (Alembic) with SQLAlchemy.

**Initial Setup**
```bash
python manage.py db init
python manage.py db migrate -m "Initial migration"
python manage.py db upgrade
```

**After Model Changes**
```bash
python manage.py db migrate -m "Describe change"
python manage.py db upgrade
```

**Mark the migration as applied in the event of something going wrong when developing**
```bash
python manage.py db stamp head
```

Rollback:
```bash
python manage.py db downgrade
```

Seed Initial data:
```bash
python manage.py seed-db
```

---

## Installation

```bash
git clone https://github.com/alias454/flask-aas.git
cd flask-aas
python3 -m venv .venv
source .venv/bin/activate  # Mac/Linux

pip install -r requirements.txt
cp .env.example .env

export FLASK_APP=app
flask run
```

---

### Build and Run

1. **Build the Docker image (no cache)**

```bash
docker build --no-cache -t flask-auth .
```

2. **Run the container**

```bash
docker run -d --env-file .env -p 5000:5000 --name flask-auth_container flask-auth

docker run --rm -it --env-file .env -p 5000:5000 flask-auth

docker run --rm -it --network host --env-file .env -p 5000:5000 flask-auth
```

3. **Access the app**

Open your browser and go to [http://localhost:5000](http://localhost:5000)

---

## Notes
- Seed scripts run once on clean DB
- `default_role_id` in `.env` controls default user role
- Admin panel for user/role/settings management
- Store SMTP credentials securely in environment variables

### Roadmap
- Abuse detection
- IP tracking
- Alternate registration workflows
- Admin dashboards
- OAuth / 2FA (more features)

---

## Maintenance
### Manual Log Cleanup
Keep log tables lean with the CLI cleanup command:

```bash
python manage.py cleanup-logs --days 7
```
- **`--days`** → Number of days to retain logs (default: 7)
- Deletes expired login attempts and audit records

---

- Run `cleanup-logs` regularly

- Monitor audit logs for anomalies
- Enable email verification & CAPTCHA for public reg
- Backup DB & user assets
