# app/core/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_caching import Cache
from flask_bcrypt import Bcrypt
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mailman import Mail

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[]  # Disable global default, define per-route
)

db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()
csrf = CSRFProtect()
cache = Cache()
mail = Mail()
