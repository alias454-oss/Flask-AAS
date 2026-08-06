# routes/admin/admin.py
import logging

from flask import Blueprint, render_template

from app.core.auth import admin_required, login_required
from app.core.decorators import log_view_action
from app.core.extensions import limiter
from app.core.meta import page_metadata
from app.core.security import get_client_ip
from app.core.trackers import get_admin_quick_stats

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/')
@limiter.limit("10 per minute", key_func=get_client_ip)
@log_view_action()
@login_required
@admin_required
def admin_home():
    meta = page_metadata.get("admin", {})
    quick_stats = get_admin_quick_stats()

    return render_template(
        'admin/admin.html',
        quick_stats=quick_stats,
        **meta,
    )
