# Media Storage

Flask-AAS owns the canonical host profile-image lifecycle. Application-domain media remains
plugin-owned.

## Profile-image path

The complete host profile-image directory is stored in:

```text
EnvSettings.users_stored_path
```

Fresh installations default to:

```text
uploads/users
```

Relative values resolve from the Flask-AAS project root. Absolute paths are used as configured.

This makes the default path a top-level runtime directory alongside `instance/`, rather than an
application-static directory.

## Upload normalization

The host accepts JPEG, PNG, and WebP profile images.

Submitted file extensions are not treated as authoritative. Images are decoded and validated before
use.

The normalization pipeline:

1. decodes the submitted image;
2. applies EXIF orientation;
3. enforces format/content limits;
4. center-crops to the host profile-image dimensions;
5. strips source metadata;
6. re-encodes to WebP;
7. stores the file under a generated random basename.

The database stores only the generated basename, not a user-supplied path.

## Transaction and cleanup behavior

Profile-image replacement/removal makes the database decision durable before deleting the superseded
generated file.

If the database commit fails, the previous database reference/file remains authoritative.

After a successful durable update, cleanup of the superseded file is best-effort.

Administrative image takedown follows the same database-first rule.

## Serving behavior

Flask-AAS does not expose a generic public profile-media route.

Host account/admin pages can render the canonical image internally. Application plugins decide
whether the host identity image is relevant to their public/domain presentation.

A plugin should reuse the host profile image when it represents the same user identity rather than
inventing a duplicate avatar lifecycle without a domain-specific reason.

## Plugin-owned media

Domain media is owned by the application plugin.

For example, a vehicle-classified plugin may own listing photographs independently from the host
profile-image path.

Plugin media paths, normalization, authorization, retention, and serving rules belong to the plugin
unless a future host media abstraction is deliberately introduced.

## Containers and persistence

Single-process development may use the default local `uploads/users` directory.

Container deployments should mount durable storage for host uploads when profile images must survive
container replacement.

A multi-instance deployment that allows profile-image writes must provide storage visible
consistently to every instance or introduce an explicit shared/object-storage design.

Flask-AAS does not currently claim a transparent local/S3 media abstraction.

## Security expectations

- Treat submitted filenames, media types, extensions, and decoded content as untrusted.
- Keep user-controlled strings out of filesystem path construction.
- Store generated basenames rather than submitted paths.
- Enforce filesystem containment.
- Keep mutable user media outside packaged application-static assets by default.
- Do not delete the old durable file reference before the database change succeeds.
