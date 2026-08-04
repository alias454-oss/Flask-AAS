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
| HSTS | Off | Off unless deliberately testing HTTPS | On only at an HTTPS boundary |
| `ProxyFix` | Off | Explicit hop count | Explicit hop count matching topology |
| Host validation | Optional localhost allowance | Local host allowlist | Required allowlist or canonical host |
| Cache and lockouts | In-memory allowed | In-memory allowed for one process | Shared backend for multiple workers/instances |
| Database | SQLite allowed | SQLite allowed | Deployment-specific durable database |
| SMTP | Optional or test backend | Optional | Explicitly configured if email features enabled |
| Migrations | Explicit init/generate/upgrade | Same | Clean bootstrap only until a versioned upgrade contract exists |

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

## Shared state

In-memory rate limits, lockout counters, and cache entries are acceptable for a single development process.

When the application uses multiple Gunicorn workers or multiple instances, process-local state is no longer authoritative. The deployment should use a shared backend or refuse/warn clearly when security behavior would be inconsistent.

Redis is one possible backend, not a mandatory development dependency.

## Email behavior

Development must be able to run without SMTP. Supported behavior should be explicit:

- disable email-dependent features;
- use a local capture/test backend; or
- return a visible development-only delivery result.

The application must not claim that an email was sent when template rendering or transport failed.

## Migration behavior

Convenient development commands may initialize migrations and generate revisions explicitly. During the current pre-release phase, generated migration directories are ignored and clean deployments may generate an initial schema from the live models.

This policy has a strict boundary:

1. it is valid only for disposable development and clean initial deployments;
2. it does not support trustworthy in-place upgrades;
3. concurrent bootstrap must be avoided;
4. before the first supported upgrade, reviewed migration sources must be versioned and normal startup must apply known upgrades only.

## Configuration validation goals

The future configuration layer should validate capabilities rather than relying only on a single `FLASK_ENV` string.

Examples:

- `PROXY_HOPS=0` permits direct local operation.
- `PROXY_HOPS>0` requires a documented trusted topology.
- `WORKER_COUNT>1` or a deployed mode requires a stable shared secret.
- Email verification enabled requires a functioning mail backend.
- HTTPS external URL enables secure cookies and HSTS at the correct boundary.

The base should fail only when the requested capability cannot operate safely, not merely because optional production infrastructure is absent.
