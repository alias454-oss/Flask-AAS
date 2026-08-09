# routes/account/dashboard.py
import logging
from flask import Blueprint, render_template
from flask_login import current_user

from app.core.auth import login_required

from app.core.extensions import limiter
from app.core.security import get_client_ip
from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/dashboard')
@limiter.limit("10 per minute", exempt_when=lambda: not current_user.is_authenticated)
@log_view_action()
@login_required
def dashboard():
    meta = page_metadata.get("dashboard", {})

    return render_template('account/dashboard.html', **meta)
