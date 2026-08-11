# plugins/registry.py
from app.core.extensions import db
from app.models.plugin import PluginRegistration
from app.plugins.interface import (
    ApplicationPlugin,
    PluginConfiguration,
    PluginContractError,
    validate_plugin_contract,
)
from app.plugins.migrations import PluginMigrationManager


class PluginRegistrationError(PluginContractError):
    """Raised when plugin registration conflicts with persisted host state."""


def _validate_import_path(import_path: str) -> str:
    if not isinstance(import_path, str) or not import_path.strip():
        raise PluginRegistrationError("import_path must be a non-empty string")

    import_path = import_path.strip()
    module_name, separator, attribute_name = import_path.partition(":")
    if not separator or not module_name or not attribute_name or ":" in attribute_name:
        raise PluginRegistrationError(
            "import_path must use the form 'package.module:attribute'"
        )
    return import_path


def _configuration(plugin: ApplicationPlugin) -> PluginConfiguration:
    configuration = plugin.validate_config()
    if not isinstance(configuration, PluginConfiguration):
        raise PluginContractError(
            "validate_config() must return PluginConfiguration"
        )
    if not isinstance(configuration.configured, bool):
        raise PluginContractError(
            "PluginConfiguration.configured must be a bool"
        )
    if configuration.reason is not None and not isinstance(configuration.reason, str):
        raise PluginContractError(
            "PluginConfiguration.reason must be a string or None"
        )
    if (
        configuration.admin_endpoint is not None
        and (
            not isinstance(configuration.admin_endpoint, str)
            or not configuration.admin_endpoint.strip()
            or len(configuration.admin_endpoint) > 200
        )
    ):
        raise PluginContractError(
            "PluginConfiguration.admin_endpoint must be a non-empty string of at most "
            "200 characters or None"
        )
    return configuration


def register_plugin(
    plugin: ApplicationPlugin,
    *,
    import_path: str,
) -> PluginRegistration:
    """Persist a plugin registration without enabling runtime activation.

    Registration is idempotent when the same plugin ID and import path are
    presented again. Conflicting IDs or import paths are rejected. The caller
    owns the surrounding transaction.
    """

    validate_plugin_contract(plugin)
    import_path = _validate_import_path(import_path)

    by_id = PluginRegistration.query.filter_by(plugin_id=plugin.plugin_id).first()
    by_path = PluginRegistration.query.filter_by(import_path=import_path).first()

    if by_id is not None:
        if by_id.import_path != import_path:
            raise PluginRegistrationError(
                f"Plugin ID {plugin.plugin_id!r} is already registered from "
                f"{by_id.import_path!r}"
            )
        by_id.configured = _configuration(plugin).configured
        return by_id

    if by_path is not None:
        raise PluginRegistrationError(
            f"Import path {import_path!r} is already registered as "
            f"{by_path.plugin_id!r}"
        )

    registration = PluginRegistration(
        plugin_id=plugin.plugin_id,
        import_path=import_path,
        enabled=False,
        configured=_configuration(plugin).configured,
    )
    db.session.add(registration)
    db.session.flush()
    return registration


def refresh_configuration(
    registration: PluginRegistration,
    plugin: ApplicationPlugin,
) -> PluginConfiguration:
    """Refresh the persisted configured status from current plugin reality."""

    _require_matching_plugin(registration, plugin)
    configuration = _configuration(plugin)
    registration.configured = configuration.configured
    return configuration


def enable_plugin(
    registration: PluginRegistration,
    plugin: ApplicationPlugin,
) -> PluginConfiguration:
    """Request plugin activation while preserving independent config status."""

    validate_plugin_contract(plugin)
    _require_matching_plugin(registration, plugin)

    # Enabling is the explicit trust/code-execution boundary, but it is not a
    # schema-upgrade operation. A plugin with declared migrations remains enabled
    # but unavailable until its own operator CLI advances that schema to head.
    manifest = getattr(plugin, "manifest", None)
    if manifest is not None and manifest.migrations is not None:
        manager = PluginMigrationManager(manifest)
        if not manager.schema_current():
            registration.enabled = True
            registration.configured = False
            return PluginConfiguration(
                configured=False,
                reason=(
                    f"Plugin schema is not current. Run 'python manage.py plugin run "
                    f"{plugin.plugin_id} db upgrade', then use Reload App Config."
                ),
            )

    plugin.prepare_enable()
    configuration = refresh_configuration(registration, plugin)
    registration.enabled = True
    return configuration


def disable_plugin(
    registration: PluginRegistration,
    plugin: ApplicationPlugin,
) -> PluginConfiguration:
    """Disable a plugin and clear plugin-managed persisted secrets.

    The registration, ordinary configuration, plugin-owned data, and schema are
    preserved. The caller owns the transaction so database-backed secret
    deletion and the enabled-state change can commit or roll back together.
    """

    validate_plugin_contract(plugin)
    _require_matching_plugin(registration, plugin)

    # Clear secrets before changing activation state. If cleanup fails, callers
    # can roll back without recording a successful disable operation. Plugins
    # must tolerate a schema that was never installed when no managed secret can
    # exist yet.
    plugin.clear_secrets()

    manifest = getattr(plugin, "manifest", None)
    if manifest is not None and manifest.migrations is not None:
        manager = PluginMigrationManager(manifest)
        if not manager.schema_current():
            registration.configured = False
            registration.enabled = False
            return PluginConfiguration(
                configured=False,
                reason="Plugin schema is not current.",
            )

    configuration = refresh_configuration(registration, plugin)
    registration.enabled = False
    return configuration


def _require_matching_plugin(
    registration: PluginRegistration,
    plugin: ApplicationPlugin,
) -> None:
    if registration.plugin_id != plugin.plugin_id:
        raise PluginRegistrationError(
            f"Registration {registration.plugin_id!r} does not match "
            f"plugin {plugin.plugin_id!r}"
        )
