"""Flask-AAS application plugin subsystem."""

from .manifest import (
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
)
from .interface import (
    PLUGIN_API_VERSION,
    ApplicationPlugin,
    PluginCompatibilityError,
    PluginConfiguration,
    PluginContractError,
    PluginDataset,
    PluginDatasetActionResult,
    validate_dataset_action_result,
    validate_plugin_contract,
    validate_plugin_datasets,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "PluginManifest",
    "PluginManifestError",
    "load_plugin_manifest",
    "ApplicationPlugin",
    "PluginCompatibilityError",
    "PluginConfiguration",
    "PluginContractError",
    "PluginDataset",
    "PluginDatasetActionResult",
    "validate_dataset_action_result",
    "validate_plugin_contract",
    "validate_plugin_datasets",
]
