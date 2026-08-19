# tests/fixtures/plugin_app/migrations/env.py
from alembic import context

from app.plugins.migrations import run_plugin_migration_environment


run_plugin_migration_environment(context)
