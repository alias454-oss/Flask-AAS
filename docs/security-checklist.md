# Flask Route Security Review Checklist

Use this checklist when adding or reviewing a Flask-AAS or plugin route. It is a review aid, not a
substitute for tests or threat modeling.

## 1. Authentication and authorization

- Is anonymous access intentional?
- Is authentication required where expected?
- Is authorization based on the target resource, not only the route?
- Does the database query enforce ownership or role scope?
- Are inactive, unapproved, or unverified users handled consistently?
- Does the action require fresh authentication or MFA reauthentication?
- For plugin routes, is effective plugin state enforced?
- Is route authorization explicit: public, authenticated, host-role-gated, or plugin-domain-gated?

High-risk examples include password/email changes, MFA changes, role changes, account lock/unlock,
administrative resets, and security-setting changes.

## 2. Request and input handling

- Is input parsed through one defined schema/form?
- Are length, type, range, and normalization rules explicit?
- Do password-setting paths use the canonical password policy/provider workflow?
- Are passwords treated as exact secret values without stripping or truncation?
- Are identifier lookups resistant to ownership bypass?
- Are retries and duplicate submissions safe?
- Are uploaded names, media types, and contents treated as untrusted?
- For images, is decoded content authoritative with explicit format/pixel/animation limits?
- Are user-controlled redirects restricted to local/allowed destinations?

Browser validation is not a server-side control.

## 3. State changes and CSRF

- Do cookie-authenticated state changes require CSRF protection?
- Are mutations limited to appropriate non-GET methods?
- If CSRF is exempt, what authenticates the request and prevents replay?

GET should not mutate account, role, security, or business state.

## 4. Transaction integrity

- Which database changes must succeed or fail together?
- Can audit, mail, cache, or queue helpers commit/rollback the caller's transaction?
- Are external side effects delayed until after the durable database decision where practical?
- When a row references a generated file, does failed commit preserve the referenced file?
- Does file deletion happen only after the durable reference is cleared/replaced?
- Does user-visible status distinguish queued work from completed external delivery?
- Are concurrent requests serialized or idempotent where required?

Audit helpers must not silently own business transaction boundaries.

## 5. Session and replay behavior

- Is session state rotated or cleared at the correct point?
- Can a remembered/non-fresh session perform this action?
- Are one-time tokens actually single-use?
- Can the request or token be replayed?
- Do password/MFA changes invalidate older authentication state?
- Are temporary pre-authentication states bounded by time and attempts?

## 6. Abuse controls

- Is rate limiting required?
- Is the correct key IP, account, session, action, or a combination?
- Can forwarding headers forge the apparent client address?
- Can lockout behavior be abused to deny service to another account?
- Does enforcement remain consistent across multiple workers?
- Is CAPTCHA justified and server-side?

## 7. Secrets and sensitive data

- Could passwords, reset/verification tokens, TOTP secrets, session IDs, or API keys reach logs?
- Are secret-bearing route/query parameters explicitly redacted from audit metadata?
- Are authorization and cookie headers excluded from captured metadata?
- Are audit targets stable resource identifiers rather than secret-bearing URLs?
- Are runtime-managed secrets encrypted with a key stored outside the database?
- Are blank-update and explicit-clear semantics defined?
- Are plugin-managed persisted secrets cleared atomically when required?
- Are secret values ever rendered back into forms?
- Are user-facing exception messages safe?

## 8. Audit events

For security-relevant actions, define:

- actor;
- target/subject;
- action;
- outcome;
- normalized failure reason;
- timestamp;
- trusted client address where available;
- request/correlation identifier where useful;
- before/after state for privileged changes.

Do not store raw credentials, live tokens, full form submissions, or raw database exception strings
in authoritative audit records.

## 9. Error behavior

- Does the public response avoid enumeration?
- Is operator-visible logging specific enough to diagnose the problem?
- Can a swallowed error produce a false success response?
- Can failure leave partial durable state?
- Is retry behavior defined?
- Are expected failures tested without depending on exact log text?

## 10. Templates and browser controls

- Is output escaped by default?
- Are intentional HTML fragments sanitized?
- Does CSP avoid unnecessary inline script?
- Are JavaScript handlers kept in static files?
- Are sensitive responses marked `no-store` where appropriate?
- Do cookie flags match the deployment mode?

## 11. Application-plugin boundary

For plugin host or plugin-owned code, verify:

- metadata discovery does not import plugin implementation/model code;
- a globally disabled plugin system leaves plugin runtime code inert;
- newly discovered plugins start disabled;
- **Enable** is the native-code trust boundary, not a migration operation;
- stale schema fails closed as `NEEDS_MIGRATION`;
- plugin migrations stay inside `plugin_<id>_*` ownership and their independent version table;
- unversioned existing plugin tables fail closed;
- browser migration actions are privileged, CSRF-protected, and limited to the intended target;
- structural activation/removal occurs through a fresh worker;
- disabling denies effective access immediately and preserves ordinary plugin data/schema;
- plugin-managed secrets are cleared according to the plugin contract;
- one broken optional plugin cannot take down unrelated core functionality;
- plugin package import-time side effects are minimized;
- filesystem identity and persisted `PluginRegistration` drift are handled explicitly;
- enabled Python plugins are treated as trusted native code, not sandboxed code.

See [`plugin-troubleshooting.md`](plugin-troubleshooting.md) for registry/filesystem recovery.

## 12. Required test cases

At minimum, cover the cases relevant to the route:

- anonymous request;
- authenticated normal user;
- wrong owner;
- privileged user;
- inactive/unapproved user;
- valid and malformed input;
- duplicate/replayed request;
- expired token/state;
- CSRF failure;
- rate-limit/lockout boundary;
- database failure;
- filesystem/media failure;
- external dependency failure;
- audit-write failure;
- success/failure redaction.

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
- Plugin/runtime guard:
- Required tests:
- Open risks:
```
