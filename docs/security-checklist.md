# Flask Route Security Review Checklist

Use this checklist when adding or reviewing a route. It is a review aid, not a substitute for tests or threat modeling.

## 1. Authentication and authorization

- Is anonymous access intentional?
- Is `login_required` or an equivalent guard applied where needed?
- Is authorization based on the target resource, not only the route?
- Does the query enforce ownership or role scope?
- Are inactive, unapproved, and unverified users handled consistently?
- Does the route require a fresh login for a sensitive operation?
- Does a privileged operation require stronger reauthentication?

High-risk examples:

- password or email changes;
- MFA enrollment, replacement, or disable;
- role changes;
- account lock or unlock;
- security-setting changes;
- administrative password reset.

## 2. Request and input handling

- Are all inputs parsed through one defined schema or form?
- Are length, type, range, and normalization rules explicit?
- Are identifier lookups resistant to ownership bypass?
- Are duplicate submissions and retries safe?
- Are file names, media types, and file contents treated as untrusted?
- Are user-controlled redirects restricted to local or allowed destinations?

Do not rely on HTML attributes or browser validation as the server-side control.

## 3. State changes and CSRF

- Does every cookie-authenticated state-changing request require CSRF protection?
- Are state changes limited to `POST`, `PUT`, `PATCH`, or `DELETE` as appropriate?
- Is the CSRF exemption narrowly justified for machine-to-machine routes?
- If CSRF is exempt, what authenticates the request and prevents replay?

A GET request should not mutate account, role, security, or billing state.

## 4. Transaction integrity

- Which database changes must succeed or fail together?
- Can logging, email, cache, or queue helpers commit or roll back the caller's transaction?
- Are external side effects performed before a durable database decision?
- Does the user-facing result distinguish queued work from completed external delivery?
- Is there an outbox, retry, or reconciliation mechanism when external systems are involved?
- Are concurrent requests serialized or made idempotent where required?

Audit helpers must not silently determine business transaction boundaries.

## 5. Session and replay behavior

- Does the route rotate or clear session state at the correct point?
- Are remember-cookie-restored sessions allowed to perform this action?
- Is a one-time token actually single-use?
- Can the same request or token be replayed?
- Does a password or MFA change invalidate older sessions?
- Are temporary pre-authentication states bounded by time and attempts?

Adding a random value to the same stolen session cookie does not prevent replay. JWTs also do not inherently solve replay or revocation.

## 6. Abuse controls

- Is rate limiting needed?
- What is the correct key: IP, account, session, action, or a combination?
- Can a client forge the apparent IP through forwarding headers?
- Is lockout behavior safe against denial-of-service attacks on a victim account?
- Does the control work across multiple workers?
- Is CAPTCHA justified by observed abuse, and is its answer server-side?

Per-IP controls alone do not stop distributed attacks. Per-account hard lockouts alone can be abused to lock out victims.

## 7. Secrets and sensitive data

- Could passwords, reset tokens, verification tokens, TOTP secrets, session IDs, or API keys reach logs?
- Has the route explicitly defined which request metadata is useful and safe to retain?
- Are secret-bearing path or query parameters declared for audit redaction?
- Are sensitive headers such as authorization and cookies excluded from captured request metadata?
- Are semantic audit targets stable resource identifiers rather than token-bearing URLs?
- Are secrets stored in the correct configuration layer?
- If a secret is runtime-managed, is it encrypted with a key stored outside the database?
- Are blank-update and explicit-clear semantics defined for stored secrets?
- Are secret values ever rendered back into forms?
- Are exception messages safe for the user-facing response?

## 8. Audit events

For a security-relevant action, define:

- actor;
- target or subject;
- action;
- outcome;
- normalized failure reason;
- timestamp;
- trusted client address where available;
- request or correlation ID;
- before/after state for privileged changes.

Do not record raw credentials, live tokens, or entire form submissions. Ordinary routes may retain concrete request context when the route deliberately treats it as audit-safe; token-bearing routes must declare explicit redaction for the sensitive parameter.

## 9. Error behavior

- Does the public response avoid user enumeration?
- Is the operator-visible error sufficiently specific?
- Does a helper swallow an error and allow a false success message?
- Does an exception leave partial state?
- Is retry behavior defined?
- Are expected failures tested without depending on log text?

## 10. Templates and browser controls

- Is output escaped by default?
- Are intentional HTML fragments sanitized?
- Does the CSP allow the required static resources without `unsafe-inline`?
- Are JavaScript handlers in static files rather than inline attributes?
- Are sensitive pages marked `no-store`?
- Are cookie flags appropriate to the active deployment mode?

## 11. Required route test cases

At minimum, test:

- anonymous request;
- authenticated normal user;
- wrong owner;
- privileged user;
- inactive or unapproved user where relevant;
- valid request;
- malformed request;
- duplicate request;
- expired token or state;
- CSRF failure;
- rate-limit or lockout boundary;
- database failure;
- external dependency failure;
- audit-write failure;
- success and failure redaction.

## Review record template

```markdown
### Route: `blueprint.endpoint`

- Methods:
- Authentication:
- Authorization:
- Fresh reauthentication:
- Inputs:
- State changes:
- Transaction boundary:
- External side effects:
- CSRF/replay protection:
- Rate-limit key:
- Audit event:
- Audit metadata policy:
- Redacted fields:
- Failure behavior:
- Required tests:
- Open risks:
```
