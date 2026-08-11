# models/user.py
from ipaddress import ip_address
from app.models.env_settings import EnvSettings
from app.core.extensions import db, bcrypt

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, index=True)
    ip_address = db.Column(db.String(45), nullable=True)
    username = db.Column(db.String(60), nullable=False, unique=True)
    hashed_password = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    auth_version = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    company_name = db.Column(db.String(255), nullable=True)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    fax = db.Column(db.String(50), nullable=True)
    alt_phone = db.Column(db.String(50), nullable=True)

    country_code = db.Column(db.String(2), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    zone_code = db.Column(db.String(16), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)

    reg_date = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    last_active = db.Column(db.DateTime, nullable=True)

    activated = db.Column(db.Boolean, nullable=False, server_default=db.false())
    approved = db.Column(db.Boolean, nullable=False, server_default=db.false())
    otp_secret = db.Column(db.String(32), nullable=True)
    pending_otp_secret = db.Column(db.String(32), nullable=True)
    pending_otp_created_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_totp_counter = db.Column(db.BigInteger, nullable=True)
    mfa_enabled = db.Column(db.Boolean, nullable=False, server_default=db.false())

    notes = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(255), nullable=True)
    admin_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=db.func.now())

    roles = db.relationship('Role', secondary='user_roles', back_populates='users')
    mfa_recovery_codes = db.relationship('MfaRecoveryCode', cascade='all, delete-orphan')
    password_reset_tokens = db.relationship('PasswordResetToken', back_populates='user', cascade='all, delete-orphan')
    user_sessions = db.relationship('UserSession', back_populates='user', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<User id={self.id} username={self.username} email={self.email}>"

    # Password helpers
    def set_password(self, password: str):
        self.hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.hashed_password, password)

    # IP helpers as property getter/setter
    @property
    def ip_obj(self):
        if self.ip_address is not None:
            return ip_address(self.ip_address)
        return None

    @ip_obj.setter
    def ip_obj(self, ip_obj):
        self.ip_address = str(ip_obj)

    @property
    def login_eligibility_failure(self):
        settings = EnvSettings.get_cached_instance()  # Assuming you have only one row
        # Default to enabled if no settings found
        use_verify_email = settings.use_verify_email if settings else 1
        use_user_approval = settings.use_user_approval if settings else 1

        if use_verify_email and not self.activated:
            return "unverified"
        if use_user_approval and not self.approved:
            return "unapproved"
        return None

    @property
    def is_active(self):
        return self.login_eligibility_failure is None

    @property
    def is_authenticated(self):
        # Usually True for logged-in users
        return True

    @property
    def is_admin(self):
        return any(role.name == 'admin' for role in self.roles)

    @property
    def is_anonymous(self):
        # False for real users
        return False

    def rotate_authentication_version(self):
        """Invalidate every earlier Flask-Login session identity."""
        self.auth_version = (self.auth_version or 0) + 1
        return self.auth_version

    def bind_session_identity(self, token, record_id=None):
        """Attach the raw browser-session token only to this in-memory user."""
        self._session_token = token
        self._session_record_id = record_id

    def clear_session_identity(self):
        self._session_token = None
        self._session_record_id = None

    @property
    def session_record_id(self):
        return getattr(self, '_session_record_id', None)

    @classmethod
    def load_from_session_id(cls, session_id, *, require_session_record=True):
        """Resolve a current versioned identity and its active session record."""
        if not session_id:
            return None

        parts = str(session_id).split(':', 2)
        if len(parts) < 2:
            return None

        try:
            user_id = int(parts[0])
            auth_version = int(parts[1])
        except (TypeError, ValueError):
            return None

        user = db.session.get(cls, user_id)
        if user is None or user.auth_version != auth_version:
            return None

        if not require_session_record:
            return user

        if len(parts) != 3 or not parts[2]:
            return None

        from app.models.user_session import UserSession

        token = parts[2]
        record_id = UserSession.active_record_id(user.id, token)
        if record_id is None:
            return None

        user.bind_session_identity(token, record_id)
        return user

    def get_id(self):
        identity = f"{self.id}:{self.auth_version}"
        token = getattr(self, '_session_token', None)
        if token:
            return f"{identity}:{token}"
        return identity

    def has_role(self, role_name):
        return any(role.name == role_name for role in self.roles)
