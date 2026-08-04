# routes/logout.py
import logging
from flask import Blueprint, redirect, url_for, session
from flask_login import current_user, logout_user, login_required
from app.core.trackers import current_route, log_action_isolated, audit_activity_enabled

logger = logging.getLogger(__name__)

logout_bp = Blueprint('logout', __name__)

@logout_bp.route('/logout')
@login_required
def logout():
    user = current_user

    if audit_activity_enabled():
        log_action_isolated(
            user_id=user.id,
            action="logout",
            target=current_route()
        )

    logout_user()
    session.clear()
    return redirect(url_for('login.login'))