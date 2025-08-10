# app/core/cache.py
import logging
from flask import g
from sqlalchemy import event
from app.models import EnvSettings, Role

logger = logging.getLogger(__name__)

@event.listens_for(EnvSettings, 'after_insert')
@event.listens_for(EnvSettings, 'after_update')
@event.listens_for(EnvSettings, 'after_delete')
def invalidate_env_settings_cache(mapper, connection, target):
    EnvSettings._cached_instance = None
    logger.debug("EnvSettings cache invalidated")

def get_cached_env_settings():
    if not hasattr(g, '_env_settings'):
        g._env_settings = EnvSettings.get_cached_instance()
    return g._env_settings


@event.listens_for(Role, 'after_insert')
@event.listens_for(Role, 'after_update')
@event.listens_for(Role, 'after_delete')
def invalidate_role_cache(mapper, connection, target):
    Role._cached_instance = None
    logger.debug("Role cache invalidated")

def get_cached_roles():
    if not hasattr(g, '_roles'):
        g._roles = Role.get_cached_instance()
    return g._roles