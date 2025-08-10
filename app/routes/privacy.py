# routes/index.py
import logging
from flask import Blueprint, render_template

from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

privacy_bp = Blueprint('privacy', __name__)

@privacy_bp.route('/privacy')
@log_view_action()
def privacy():
    meta = page_metadata.get("privacy", {})

    return render_template("privacy.html", **meta)