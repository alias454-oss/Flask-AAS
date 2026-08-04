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
- `ProxyFix` enabled without explicit configuration.

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
- Token-bearing routes declare redaction and no concrete token appears anywhere in the stored audit row.
- No concrete reset or verification token appears in audit rows.
- Audit helpers do not call transaction-ending methods.
- Direct-development mode starts without SMTP, Redis, certificates, or a supplied secret.
- Production mode rejects missing stable secrets.
- Proxy trust is disabled unless configured.
- HSTS and secure cookies are not forced during direct HTTP development.
- All registered routes resolve their endpoint references.
- All email templates referenced by code exist.
- Migrations upgrade a new database and a representative prior schema.
- CSP tests cover interactive controls without allowing inline script.

## Larger platforms

SonarQube, commercial SAST, or broader application-security platforms may be useful later, but they are not prerequisites for this project. Add them only when they provide a concrete benefit beyond the baseline and do not create an unmaintained findings queue.
