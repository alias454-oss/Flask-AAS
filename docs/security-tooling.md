# Security Tooling Baseline

Security tooling supports review and regression testing. It does not replace code-level reasoning,
threat modeling, or tests.

## Dependency baseline

Flask-AAS currently uses:

- Python 3.13;
- `pyproject.toml` for direct dependencies;
- a hash-pinned `requirements.txt` deployment lock;
- Argon2id through `argon2-cffi` for password hashing;
- bcrypt only for transitional verification/upgrade of legacy hashes;
- Psycopg 3 for PostgreSQL;
- standard-library `zoneinfo` with `tzdata`;
- Pillow for image/CAPTCHA processing;
- a Python slim Trixie container base.

Release validation should install the lock with:

```bash
python -m pip install \
  --require-hashes \
  --only-binary=:all: \
  -r requirements.txt
```

Audit the resolved lock before release checkpoints.

## Recommended tools

### Ruff

Use for fast Python linting, imports, and straightforward correctness/style checks. Keep the enabled
rule set small enough that findings remain actionable.

### Bandit

Use for basic Python security-pattern detection. Treat findings as review leads, not proof of a
vulnerability.

### Semgrep

Use for framework-aware and project-specific checks.

Useful Flask-AAS rules include:

- token-bearing routes without explicit audit redaction;
- audit helpers calling `commit()`/`rollback()` on caller-owned transactions;
- state-changing GET routes;
- CSRF-exempt browser mutations;
- sensitive actions without required reauthentication;
- request metadata capture that includes authorization/cookie headers;
- `ProxyFix` enabled without explicit topology;
- disabled-plugin startup importing plugin implementation/model modules;
- plugin discovery escaping the intended immediate-child manifest boundary;
- plugin operational logging that records arbitrary command arguments.

### Dependency audit

For every relevant dependency finding, record:

- package and resolved version;
- whether the affected code path is used;
- fixed version;
- compatibility impact;
- remediation or accepted-risk decision.

### CodeQL or equivalent

Use repository-level data-flow scanning for injection and cross-function issues that local linters
may miss.

### Pytest

The regression suite is the primary executable security control. Static tools cannot prove session
state, transaction ownership, token replay resistance, authorization, or deployment-mode behavior.

## Security invariants worth automating

### Authentication and sessions

- Every submitted login produces exactly one final `AuditLogin` outcome.
- Successful login audit rows are written only after authentication succeeds.
- Sensitive MFA changes require appropriate reauthentication.
- Forced full-login paths clear remembered authentication state.
- Accepted TOTP counters cannot be replayed.
- Remembered inactivity downgrades to non-fresh authentication and stops the boundary-crossing
  mutation.
- Password changes invalidate earlier session identities and outstanding reset links.
- Administrator-selected credentials require owner replacement after complete authentication/MFA.
- Production bootstrap credentials set `must_change_password`; development/testing bootstrap avoids
  that ceremony without weakening administrator-created-user behavior.
- User-selected passwords clear provisioned-credential state, while hash-format-only upgrades preserve
  it.
- Forced password-change state cannot be bypassed through unrelated authenticated routes.
- Password-reset tokens are hashed at rest and accepted only once.

### Audit and secrets

- Token-bearing routes declare redaction.
- Concrete reset/verification tokens never appear in stored audit rows.
- Audit helpers do not end caller-owned business transactions.
- Authoritative audit metadata does not persist raw database exception strings.
- SMTP credentials, passwords, tokens, cookies, and authorization headers do not appear in logs or
  rendered forms.

### Deployment

- Direct development starts without SMTP, Redis, certificates, or a supplied secret.
- Production/multi-worker deployment requires stable shared secrets/state where needed.
- Proxy trust is disabled unless explicitly configured.
- Clean empty databases reach bootstrap without querying missing persisted-settings tables.
- SQLite and PostgreSQL clean-bootstrap paths remain viable.
- Shipped migration checkpoints can construct clean databases without generating migration history at
  deployment time.
- HSTS/secure-cookie behavior matches the external HTTP/HTTPS boundary.

### Email

- Required email verification cannot be enabled without usable outbound email.
- Runtime SMTP overrides are complete-or-rejected rather than field-blended.
- UI-managed SMTP credentials remain encrypted and are not rendered back.
- Queued email is not represented as completed SMTP delivery.

### Application plugins

- Global plugin disable leaves core startup independent of plugin runtime code.
- Registered-but-disabled plugins do not import implementation/models or register routes.
- Plugin manifests/migration declarations can be inspected without implementation import.
- Explicit enablement is the code-execution boundary and does not migrate schema.
- Core Alembic does not adopt plugin-owned namespaces.
- Plugin migration failure does not falsely advance the plugin version table.
- Existing unversioned plugin tables fail closed.
- Disabled plugins deny access immediately and are removed structurally after reload.
- Disabling preserves ordinary plugin data/schema while clearing managed secrets as required.
- Incompatible/failed optional plugins do not prevent core startup.
- Registry/filesystem identity drift is handled without deleting unrelated plugin-owned data.

## Suggested CI stages

1. Install from the canonical lock.
2. Lint.
3. Run unit/integration tests.
4. Run migration/database tests.
5. Audit dependencies.
6. Run Python SAST.
7. Run repository data-flow scanning.
8. Build the container and run a clean configuration/bootstrap smoke test.

Changes affecting SQL or migration behavior should include PostgreSQL validation.

## Larger platforms

SonarQube, commercial SAST, and broader application-security platforms can be added when they provide
clear value. Do not add tooling that only creates an unmaintained findings queue.
