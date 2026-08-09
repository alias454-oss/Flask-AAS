# Development and Deployment Modes

Flask-AAS should be easy to run locally and strict when deployed. Production requirements must not be imposed blindly on development.

## Design principle

```text
low-friction development
+ explicit deployment mode
+ validation at the deployment boundary
```

The application does not require internal certificates for ordinary development or for a trusted proxy-to-application hop. TLS can terminate at Caddy, Nginx, a load balancer, or another external boundary while the isolated internal hop remains HTTP.

## Target configuration matrix

| Capability | Direct development | Development behind local proxy | Production or multi-worker |
|---|---|---|---|
| Application URL | `http://127.0.0.1:5000` or similar | Local proxy URL | Explicit canonical external URL |
| TLS | Not required | Optional at proxy | Required at the external boundary |
| Internal proxy-to-app TLS | Not required | Not required | Optional; topology-dependent |
| `SECRET_KEY` | Generated fallback allowed | Generated or local persisted key | Stable externally supplied shared key |
| Secure cookies | Off for HTTP | Match proxy scheme | On when external scheme is HTTPS |
| `HttpOnly` | On | On | On |
| `SameSite` | `Lax` by default | `Lax` by default | Policy-defined, normally `Lax` |
| Session inactivity | Local default or disabled deliberately | Same | Explicit timeout appropriate to the application |
| HSTS | Off | Off unless deliberately testing HTTPS | On only at an HTTPS boundary |
| `ProxyFix` | Off | Explicit hop count | Explicit hop count matching topology |
| Host validation | Optional localhost allowance | Local host allowlist | Required allowlist or canonical host |
| Cache and lockouts | In-memory allowed | In-memory allowed for one process | Shared backend for multiple workers/instances |
| Database | SQLite allowed | SQLite allowed | Deployment-specific durable database |
| Outbound email | Optional; disabled or mock delivery | Optional; deployment SMTP or UI override | Explicitly configured when email-dependent features are enabled |
| Migrations | Explicit init/generate/upgrade | Same | Clean bootstrap only until a versioned upgrade contract exists |
| Application plugins | Optional; global host may remain disabled | Same | Explicitly enabled applications activate at a Gunicorn reload boundary |

## Secret-key behavior

### Development

When no `SECRET_KEY` is supplied, the application may generate one automatically. This is safer and more usable than failing startup for a disposable development server.

Acceptable development options:

1. Generate an ephemeral key on each start.
2. Generate a local key once under an ignored `instance/` path so sessions survive reloads.
3. Supply a development value through an ignored `.env` file.

The application should clearly log which behavior is active without printing the secret.

### Production and multiple workers

Every process must share the same stable key. Startup should fail clearly when production or multi-worker mode lacks one.

A random per-process fallback in this mode causes inconsistent sessions and invalid tokens across workers and restarts.

## Proxy behavior

### Direct development

Do not enable `ProxyFix`. Ignore client-supplied forwarding headers and use the direct peer address.

### Known proxy

Enable only the forwarded fields and hop counts that the topology actually supplies. Restrict direct access to the Flask/Gunicorn port when forwarded headers are trusted.

`TRUSTED_PROXIES` and `ProxyFix` must describe the same trust boundary. A custom IP parser must first establish that the immediate peer is trusted before considering forwarded client values.


### Current proxy checkpoint

`PROXY_HOPS` defaults to `0`, so direct development does not install `ProxyFix` and ignores spoofed forwarding headers for audit, tracking, and rate-limit identity.

When `PROXY_HOPS` is greater than zero, the configured hop count enables `ProxyFix`, while `TRUSTED_PROXIES` controls whether the immediate peer is allowed to supply forwarded client addresses. Host allowlisting and canonical external URL generation remain separate deployment-hardening work.

## Cookies and HSTS

`Secure` cookies are not sent over plain HTTP. They must remain disabled for direct HTTP development.

`HttpOnly` and a reasonable `SameSite` policy should remain enabled in development.

HSTS must not be sent by a direct HTTP development server. Browsers can cache HSTS state and make local testing unnecessarily difficult.

## Session inactivity

`SESSION_INACTIVITY_TIMEOUT_SECONDS` defines a sliding inactivity window for an authenticated browser session. The default is 900 seconds. A value of `0` disables this control.

The session stores a numeric Unix timestamp and refreshes it on authenticated application requests. Static asset requests do not extend the window. At the timeout boundary, Flask-AAS logs out the user, clears transient MFA and other session state, and requests deletion of the Flask-Login remember cookie.

A session restored from a valid remember cookie has no prior browser-session activity timestamp. It begins a new inactivity window and remains non-fresh, so existing fresh-login and MFA reauthentication controls continue to protect sensitive operations. Pre-authentication MFA state is not treated as an authenticated session and remains governed by its own expiry and attempt limits.

This control applies to the current browser session. Enforcing inactivity across a browser that has discarded its session cookie but still holds an otherwise valid remember cookie requires the future persisted session-management work tracked separately.

## Password policy

The deployment `PASSWORD_*` values are clean-install seed defaults. Once the
`EnvSettings` row exists, **Admin -> Site Settings -> Password Policy** is the
authoritative runtime source. Administrators can change the minimum length and
optional composition requirements without restarting Flask-AAS.

The default minimum is 20 characters. Spaces and long passphrases are valid,
there is no password-policy maximum length, and existing stored passwords are
not retroactively evaluated when the policy changes. Generated passwords use
the same active persisted policy.

## Shared state

In-memory rate limits, lockout counters, and cache entries are acceptable for a single development process.

When the application uses multiple Gunicorn workers or multiple instances, process-local state is no longer authoritative. The deployment should use a shared backend or refuse/warn clearly when security behavior would be inconsistent.

Redis is one possible backend, not a mandatory development dependency.

## Application-plugin hosting

Application hosting is optional. The database-backed **Enable Application Plugins** setting is the global host switch. When it is disabled, Flask-AAS continues to provide its normal authentication, account, audit, contact, and administrative core without loading application-plugin runtime code.

Bundled applications may have registration rows so the host can present their metadata after the plugin system is enabled. Registration is metadata only. A registered but disabled application must not import its plugin implementation or model modules, deploy plugin-owned schema, register routes, or contribute navigation during ordinary application startup.

Enabling an application is the explicit trust/code-execution boundary. At that point Flask-AAS may import that selected plugin's Python implementation and inspect its declared migration/configuration state. **Enable does not create, migrate, or stamp plugin-owned schema.** Python plugins execute with the permissions of the Flask-AAS process; enabling one is therefore equivalent to trusting native application code. Flask-AAS does not claim to sandbox enabled plugins.

Schema readiness, configuration readiness, and structural runtime activation are separate boundaries:

```text
registered + disabled
        |
        v
administrator enables application
        |
        v
Reload App Config
        |
        +--> NEEDS_MIGRATION
        |         |
        |         v
        |    explicit plugin schema upgrade
        |         |
        |         v
        |    Reload App Config
        |
        +--> NEEDS_CONFIGURATION
        |         |
        |         v
        |    plugin-owned configuration
        |         |
        |         v
        |    Reload App Config
        |
        v
ACTIVE
```

A persisted schema/configuration change does not mutate an already running worker's plugin status snapshot. Schema migration from `NEEDS_MIGRATION` still requires a fresh worker before the plugin can be structurally registered. For a plugin that is already structurally loaded as `ACTIVE` or `NEEDS_CONFIGURATION`, request and navigation access follow the current persisted `enabled/configured` flags immediately; **Reload App Config** reconciles the worker's startup-time status and any startup-only plugin behavior.

The repository container runs Gunicorn as the unprivileged `flaskaas` user. **Reload App Config** uses a fixed `SIGHUP` to the Gunicorn master at PID 1 after verifying that PID 1 is Gunicorn; it does not invoke a shell or accept an arbitrary process or signal. If that deployment shape is not present, the action fails normally rather than attempting an unsafe fallback.

Disabling an application immediately removes effective route/navigation access through the host guard, clears plugin-managed persisted secrets as part of the disable transaction, and preserves ordinary plugin configuration, schema, and business data. After **Reload App Config**, the fresh worker no longer imports or structurally registers the disabled application.

Current bundled plugins are trusted code shipped with the Flask-AAS deployment. Future externally supplied plugins must require an explicit install/trust action before any plugin Python is imported. Filesystem presence alone must never imply trust, registration, enablement, or runtime activation.

## Email behavior

Development must be able to run without SMTP. Outbound email is controlled by the database-backed **Enable Outbound Email** switch. When it is off, no message is queued, including in debug mode.

The effective transport is resolved for each dispatch in this order:

1. `MAIL_DEBUG=true` provides mock delivery after the master switch is enabled. Messages are rendered and reported as queued, but no SMTP connection is made.
2. When `MAIL_CONFIG_UI_ENABLED=true`, a complete Site Settings SMTP configuration overrides deployment values.
3. Otherwise, a complete deployment configuration using `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS` or `MAIL_USE_SSL`, optional paired credentials, and `MAIL_DEFAULT_SENDER` is used.
4. When no complete source exists, delivery is unavailable.

Site Settings and deployment values are not blended field by field. An empty Site Settings override falls back to deployment configuration, while a partial override is rejected. UI-managed SMTP passwords are encrypted with `MAIL_CONFIG_ENCRYPTION_KEY`, which must remain outside the database. The stored password is not rendered back into the form, and clearing the override is an explicit operation.

`Require Email Verification` depends on enabled outbound email and an available effective transport. This prevents registration from creating users who cannot receive their activation link.

Mail dispatch remains asynchronous. A route may report that a message was queued after the background worker starts; that does not mean the SMTP server accepted or delivered it. Final delivery success or failure is recorded by the worker. Template rendering, policy lookup, and thread-start failures remain visible to callers.

## Migration behavior

Convenient development commands may initialize migrations and generate revisions explicitly. During the current pre-release phase, generated migration directories are ignored and clean deployments may generate an initial schema from the live host models.

This policy has a strict boundary:

1. it is valid only for disposable development and clean initial deployments;
2. it does not support trustworthy in-place upgrades;
3. concurrent bootstrap must be avoided;
4. before the first supported upgrade, reviewed migration sources must be versioned and normal startup must apply known upgrades only.

Plugin registration does not import plugin models merely so Alembic can see them. Plugin API v1 now provides an independent plugin migration mechanism driven by static `plugin.toml` metadata. A plugin may declare a package-local migration directory such as `migrations = "migrations"`; the host derives a portable table namespace `plugin_<id>_*` and an independent version table `plugin_<id>_alembic_version`.

The plugin migration manager follows these rules:

- a fresh namespace with no plugin-owned tables and no plugin version table may create the current plugin model schema and stamp the current head;
- an existing versioned plugin runs its own Alembic history normally;
- existing plugin-owned tables without the plugin version table fail closed instead of being blindly stamped;
- migration/autogenerate is constrained to the plugin-owned table prefix;
- migration operations are explicit and do not occur merely because the plugin was enabled;
- a disabled plugin remains inert during ordinary web startup; explicit operator migration CLI is a separate deliberate code-execution boundary.

Development migration directories remain disposable/ignored during the current pre-release phase and may be regenerated or squashed. At the first supported release/checkpoint, published plugin schema transitions become durable upgrade history.

`AAS-039` still requires final release-grade acceptance proving core Alembic preserves its own `plugin_registrations` table while excluding plugin-owned namespaces such as `plugin_example_*`, a representative `0001 -> 0002` upgrade reaches the same final schema as greenfield bootstrap, failed migrations do not falsely advance the plugin version table, and focused PostgreSQL lifecycle/migration coverage is green.

## Configuration validation goals

The future configuration layer should validate capabilities rather than relying only on a single `FLASK_ENV` string.

Examples:

- `PROXY_HOPS=0` permits direct local operation.
- `PROXY_HOPS>0` requires a documented trusted topology.
- `WORKER_COUNT>1` or a deployed mode requires a stable shared secret.
- Enabling outbound email requires debug delivery or a complete deployment or Site Settings transport.
- Email verification enabled requires outbound email and a functioning effective mail backend.
- UI-managed SMTP credentials require `MAIL_CONFIG_UI_ENABLED=true` and an external Fernet encryption key.
- HTTPS external URL enables secure cookies and HSTS at the correct boundary.
- The global application-plugin switch may remain off without importing plugin runtime code.
- Enabling an individual application is an explicit native-code trust boundary; schema migration and runtime activation remain separate explicit operations.
- Structural plugin changes are realized through a fresh Gunicorn worker rather than live Blueprint mutation.

The base should fail only when the requested capability cannot operate safely, not merely because optional production infrastructure is absent.
