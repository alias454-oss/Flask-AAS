# app/core/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mailman import Mail

from app.core.migrations import (
    core_migration_include_name,
    core_migration_include_object,
)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["500 per minute"]  # Global default, define stricter limits per-route if needed
)

db = SQLAlchemy()
migrate = Migrate(
    include_name=core_migration_include_name,
    include_object=core_migration_include_object,
)
csrf = CSRFProtect()
cache = Cache()
mail = Mail()
