# Email and Verification

Flask-AAS can run without SMTP. Mail-dependent features become available only when outbound email is
enabled and an effective transport exists.

## Master switch

Outbound mail is controlled by:

**Admin → Site Settings → Enable Outbound Email**

When the switch is off, messages are not queued, including when debug delivery is configured.

Features that require email, such as required account verification or the public contact form, must
also have an effective mail transport.

Disabling outbound email does not disable administrator-created accounts. An administrator can
create a user with a password and deliver that credential through a separate channel. Flask-AAS
marks administrator-selected passwords for replacement and requires the user to choose a private
password after login. The registration form continues to label the field simply **Password**.

## Transport precedence

The effective transport is resolved for each dispatch in this order:

1. **Outbound email disabled** → no delivery.
2. `MAIL_DEBUG=true` → mock/debug delivery; messages are rendered but no SMTP connection is made.
3. Complete Site Settings SMTP configuration when UI-managed mail is enabled.
4. Complete deployment/environment SMTP configuration.
5. Otherwise delivery is unavailable.

Site Settings and environment SMTP values are not blended field by field.

An empty Site Settings override falls back to deployment configuration. A partial override is
rejected.

## Deployment SMTP

Typical external configuration:

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

Credentials should remain in deployment secret/configuration storage rather than source control.

## Site Settings SMTP override

Runtime SMTP editing is disabled by default.

To permit it:

```dotenv
MAIL_CONFIG_UI_ENABLED=true
MAIL_CONFIG_ENCRYPTION_KEY=<fernet-key>
```

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The encryption key must remain outside the database.

UI-managed SMTP passwords are:

- encrypted before persistence;
- never rendered back into the form;
- not included in audit metadata;
- changed only when a new value is explicitly supplied;
- cleared through the explicit override-clear operation.

## Dispatch behavior

Mail dispatch is asynchronous.

A route can report that a message was **queued** after the background worker starts. This does not
mean the SMTP server accepted or delivered the message.

Dispatch outcomes exposed to callers are intentionally bounded:

- `queued`
- `disabled`
- `failed`

Final SMTP success or failure is recorded by the worker/operator logs.

Template rendering, policy resolution, and thread-start failures remain visible to the caller rather
than being silently treated as success.

## Email verification

Required email verification uses the persisted account activation state.

It can be enabled only when:

- outbound mail is enabled; and
- an effective transport is available.

This avoids creating users who are required to activate an account but cannot receive an activation
message.

Verification handling is idempotent and safely handles malformed, expired, missing-account, and
already-used tokens.

## Password-related notifications

Password reset and password-change mail follow the same transport resolution.

Password-reset secrets are not stored or logged in plaintext. Password-change notification is
queued only after the password transaction commits.

When outbound email is disabled, the normal login page hides the **Forgot Password** link and
password-change notifications are treated as intentionally unavailable rather than operational
failures. Administrator account creation with an explicit password remains available. The
blank-password account-creation path still requires email because it issues a setup link.

## Contact form

The public contact form is disabled unless the deployment has:

- a configured Admin Email;
- outbound email enabled;
- an effective mail transport.

If those dependencies become unavailable:

- `/contact` returns 404;
- the Contact navigation entry is hidden;
- the route is omitted from the sitemap.

When spam checking is enabled, contact submissions are evaluated by the selected spam-check provider.
The built-in local provider uses packaged phrase data and performs no network request.

Spam-provider runtime failure is logged and fails open so an optional spam service does not take the
contact form offline.

## Operator test

Test the effective mail configuration with:

```bash
python manage.py mail-test
python manage.py mail-test --to operator@example.com
```

The command exits unsuccessfully when mail is disabled, unavailable, or cannot be queued.

## Security expectations

- Never log SMTP passwords or mail tokens.
- Do not place credentials in command-line arguments.
- Keep `MAIL_CONFIG_ENCRYPTION_KEY` outside the database.
- Treat queued mail as queued work, not completed delivery.
- Keep verification/reset tokens out of authoritative audit metadata.
