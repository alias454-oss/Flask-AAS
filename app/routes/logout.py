# routes/logout.py
import logging

from flask import Blueprint, redirect, session, url_for
from flask_login import current_user, login_required, logout_user

from app.core.sessions import close_current_session
from app.core.trackers import audit_activity_enabled, current_route, log_action_isolated

logger = logging.getLogger(__name__)

logout_bp = Blueprint('logout', __name__)


@logout_bp.route('/logout')
@login_required
def logout():
    user = current_user

    if audit_activity_enabled():
        log_action_isolated(
            user_id=user.id,
            action='logout',
            target=current_route(),
        )

    close_current_session()
    logout_user()
    session.clear()
    session['_remember'] = 'clear'
    return redirect(url_for('login.login'))
