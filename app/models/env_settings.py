# models/env_settings.py
from app.core.extensions import db

class EnvSettings(db.Model):
    __tablename__ = 'env_settings'

    _cached_instance = None  # class-level cache

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', name='fk_user_profiles_user_id_users'), nullable=False, unique=True)

    # Basic Site Identity
    site_name = db.Column(db.String(50), nullable=False)
    site_url = db.Column(db.String(100))
    site_lang = db.Column(db.String(5), nullable=False, default='en')
    site_timezone = db.Column(db.String(60), nullable=False, server_default="")
    description = db.Column(db.Text, nullable=False, server_default="")
    keywords = db.Column(db.Text, nullable=False, server_default="")

    # Admin Contact
    admin_name = db.Column(db.String(50))
    admin_email = db.Column(db.String(100))

    # User System
    site_mode = db.Column(db.SmallInteger, nullable=False, default=0)  # 0 = public/multi-user, 1 = single-user
    default_role_id = db.Column(db.Integer, db.ForeignKey('roles.id', name='fk_env_settings_default_role_id_roles'), nullable=True)
    users_per_page = db.Column(db.SmallInteger, nullable=False, default=20)
    users_stored_path = db.Column(db.String(255), nullable=False)

    # Lockout settings for security
    max_failed_attempts = db.Column(db.Integer, nullable=False, default=5, server_default=db.text('5'))
    lockout_duration_seconds = db.Column(db.Integer, nullable=False, default=900, server_default=db.text('900')) # 15 minutes

    # Behavior Toggles (Booleans for clarity)
    use_mfa = db.Column(db.Boolean, nullable=False, server_default=db.false())
    use_verify_email = db.Column(db.Boolean, nullable=False, server_default=db.false())
    use_user_approval = db.Column(db.Boolean, nullable=False, server_default=db.false())
    use_user_location = db.Column(db.Boolean, nullable=False, server_default=db.true())
    use_captcha = db.Column(db.Boolean, nullable=False, server_default=db.true())
    contact_enabled = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    maint_mode = db.Column(db.Boolean, nullable=False, server_default=db.false())
    visitor_tracking = db.Column(db.Boolean, nullable=False, server_default=db.false())
    use_fancy_urls = db.Column(db.Boolean, nullable=False, server_default=db.false())
    enable_plugins = db.Column(db.Boolean, nullable=False, server_default=db.false())

    # User Cleanup Policies
    enable_delete_old_users = db.Column(db.Boolean,nullable=False, server_default=db.false())
    users_delete_after_days = db.Column(db.SmallInteger, nullable=False, default=15)
    email_after_days = db.Column(db.SmallInteger, nullable=False, default=45)

    # Templating / Display
    template = db.Column(db.String(100), nullable=True)

    # Email / SMTP Config
    # use_smtp is the application-level outbound email switch. Connection
    # details come from a complete UI override or the deployment environment.
    use_smtp = db.Column(db.Boolean, nullable=False, server_default=db.false())
    smtp_host = db.Column(db.String(255))
    smtp_port = db.Column(db.Integer, nullable=True)
    smtp_security = db.Column(db.String(10), nullable=False, default="starttls", server_default="starttls")
    smtp_user = db.Column(db.String(255))
    smtp_pass = db.Column(db.Text)
    smtp_default_sender = db.Column(db.String(255))

    # Advanced / Optional Features
    enable_analytics = db.Column(db.Boolean, nullable=False, server_default=db.false())
    allow_custom_themes = db.Column(db.Boolean, nullable=False, server_default=db.false())
    enable_logging = db.Column(db.Boolean,nullable=False, server_default=db.true())
    log_level = db.Column(db.String(10), nullable=False, server_default="INFO")

    def __repr__(self):
        return f"<EnvSettings site_name='{self.site_name}', mode={self.site_mode}, user_id={self.user_id}>"

    @property
    def allow_registration(self):
        # 0 = public/multi-user, 1 = single-user
        # If site_mode is 0(public/multi-user mode) → returns True
        # If site_mode is 1(private/single-user mode) → returns False
        return self.site_mode == 0

    @classmethod
    def get_instance(cls):
        cls._cached_instance = cls.query.first()
        return cls._cached_instance

    @classmethod
    def get_cached_instance(cls):
        if cls._cached_instance is None:
            cls._cached_instance = cls.query.first()
        return cls._cached_instance

    @classmethod
    def is_audit_activity_logging_enabled(cls):
        env = cls.get_cached_instance()
        return env.enable_logging if env else False

    @classmethod
    def is_audit_login_logging_enabled(cls):
        env = cls.get_cached_instance()
        return env.enable_logging if env else False

    @classmethod
    def is_visitor_tracking_enabled(cls):
        env = cls.get_cached_instance()
        return env.visitor_tracking if env else False

    @classmethod
    def is_user_location_enabled(cls):
        env = cls.get_cached_instance()
        return env.use_user_location if env else False

    @classmethod
    def get_max_failed_attempts(cls) -> int:
        env = cls.get_cached_instance()
        return env.max_failed_attempts if env else 5

    @classmethod
    def get_lockout_duration_seconds(cls) -> int:
        env = cls.get_cached_instance()
        return env.lockout_duration_seconds if env else 900
