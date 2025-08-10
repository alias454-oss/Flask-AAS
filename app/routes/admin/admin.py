# routes/admin/admin.py
import logging
from flask import Blueprint, render_template
from app.core.auth import login_required, admin_required
from app.core.meta import page_metadata
from app.core.decorators import log_view_action
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.models import User, Role, OnlineUser

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/')
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
@admin_required
def admin_home():
    meta = page_metadata.get("admin", {})

    total_sellers_user_count = (
        db.session.query(User)
        .join(User.roles)
        .filter(Role.name == 'seller')
        .count()
    )

    stats = {
        'total_registered_user_count': User.query.count(),
        'total_pending_user_count': User.query.filter_by(is_active=False).count(),
        'total_sellers_user_count': total_sellers_user_count,
        'total_online_user_count': OnlineUser.query.count(),
        'total_guest_online_user_count': OnlineUser.query.filter_by(user='guest').count()
        # Add other stats here as needed
    }
    return render_template('admin/admin.html', version='v1.0', **meta, **stats)
