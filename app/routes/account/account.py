# routes/account/account.py
import logging
from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app.core.extensions import limiter
from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

account_bp = Blueprint('account', __name__)

@account_bp.route('/account')
@limiter.limit("10 per minute", exempt_when=lambda: not current_user.is_authenticated)
@log_view_action()
@login_required
def account():
    meta = page_metadata.get("account", {})

    return render_template("account/account.html", **meta)