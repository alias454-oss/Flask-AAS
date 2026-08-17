# Authentication, MFA, and Sessions

This document describes the Flask-AAS authentication and account-security behavior that application
code can rely on.

## Password hashing and policy

New passwords are hashed with Argon2id through `argon2-cffi`.

Legacy Flask-Bcrypt hashes remain verifiable and are upgraded to the current hashing format after a
successful login.

Password policy is persisted in Site Settings. Deployment `PASSWORD_*` values are clean-install seed
defaults; after the settings row exists, **Admin → Site Settings → Password Policy** is
authoritative.

The default minimum password length is 20 characters. Spaces and long passphrases are valid. There
is no policy maximum intended to truncate user passwords.

Every password-setting path should use the same active policy, including:

- registration;
- password reset;
- authenticated password change;
- administrator-set/generated passwords.

Passwords are exact secret values. They must not be silently stripped or truncated.

## Password-check providers

Password checking is optional and disabled by default.

When enabled, candidate passwords pass through the selected provider after local policy validation.
The built-in `local` provider uses a packaged common-password blocklist and performs no network
request.

Additional providers implement the shared provider contract instead of adding provider-specific code
to password-setting routes.

Provider implementations should return only the decision/message needed by the core and should not
log or persist the submitted password.

## Login behavior

Login auditing records one final `AuditLogin` outcome per submitted attempt.

Public login responses should remain resistant to account enumeration even when internal audit/log
records contain a normalized reason useful to operators.

Successful authentication establishes the Flask-Login identity and the corresponding durable session
state before success is treated as complete.

## Session tracking and revocation

Flask-AAS keeps durable `UserSession` state in addition to browser-session state.

Durable session records support:

- active-session display;
- explicit session revocation;
- revoke-other-sessions behavior;
- password-change/reset invalidation;
- normal logout/session termination.

Changing a password rotates the user's authentication version so older active sessions and remember
cookies are invalidated across clients.

## Sliding inactivity timeout

`SESSION_INACTIVITY_TIMEOUT_SECONDS` defines the authenticated browser-session inactivity window.

The default is 900 seconds. Set it to `0` to disable the inactivity control.

The timer is sliding and refreshes on authenticated application requests. Static asset requests do
not extend the window.

### Normal sessions

When a non-remembered session crosses the inactivity boundary:

- Flask-Login state is cleared;
- the current durable browser session is ended;
- a full login is required.

### Remembered sessions

A remembered session is downgraded rather than forgotten:

- the remember cookie remains valid;
- the durable remembered session remains valid;
- transient application/MFA browser state is cleared;
- the identity remains available as **non-fresh** authentication.

The request that crosses the boundary is stopped with HTTP `303` before route handling continues.
This is important for `POST` and other state-changing requests: stale authentication is not allowed
to carry a mutation across the timeout boundary.

The redirect goes to a safe GET destination rather than replaying the mutation URL.

A session restored from a remember cookie starts a new inactivity window and remains non-fresh.

## Fresh authentication

Sensitive operations may require a fresh login rather than merely an authenticated remembered
identity.

Examples include:

- MFA enrollment/replacement;
- recovery-code rotation;
- MFA disable;
- other security-sensitive account changes.

Remember-cookie restoration does not satisfy a fresh-login requirement.

## TOTP MFA

Flask-AAS supports TOTP enrollment and authenticator replacement.

Temporary MFA state is bounded by timestamps and attempt limits. Terminal MFA failure returns the
user to a complete login and removes remembered authentication state.

The accepted TOTP counter is persisted so the same accepted code cannot be replayed.

Final MFA login state is only treated as successful after required persistence succeeds.

## Recovery codes

Recovery codes are:

- generated with sufficient randomness;
- shown to the user when issued;
- stored only in hashed form;
- single-use;
- explicitly rotatable.

An unused recovery code may be accepted where the MFA workflow permits it, such as sensitive MFA
reauthentication/disable.

## Password reset

Password reset uses high-entropy opaque secrets.

The live token is not stored. The database stores a SHA-256 hash plus explicit:

- expiry;
- consumption;
- revocation state.

Token consumption uses a conditional database update so only one concurrent request can succeed.

Requesting another reset link does not automatically revoke an earlier valid link through an
unauthenticated request.

A successful reset:

1. changes the password;
2. consumes the used reset token;
3. revokes other outstanding reset tokens;
4. rotates authentication state;
5. invalidates earlier sessions/remember cookies;
6. requires a complete login.

Authenticated password changes apply the same reset-token/session invalidation behavior.

## Email verification

Email verification uses the persisted `User.activated` account state.

Verification links are idempotent and malformed, expired, missing-account, or already-used links fail
safely.

Required verification depends on outbound email being enabled with an effective transport. Flask-AAS
does not permit a configuration that knowingly requires activation mail while no mail transport is
available.

See [`email.md`](email.md) for transport configuration.

## Security expectations for application code

Application routes should:

- use the canonical password workflow;
- use normal Flask-AAS session/fresh-login state instead of inventing parallel auth state;
- explicitly decide whether remembered/non-fresh authentication is sufficient;
- treat reset/verification/MFA secrets as redacted audit data;
- avoid copying authentication secrets into plugin-owned persistence;
- use host roles only where coarse host authorization matches the application's needs.

For route review, see [`security-checklist.md`](security-checklist.md).
