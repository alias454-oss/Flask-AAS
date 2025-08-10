# routes/tos.py
import logging
from flask import Blueprint, render_template

from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

tos_bp = Blueprint('tos', __name__)

@tos_bp.route('/tos')
@log_view_action()
def tos():
    meta = page_metadata.get("tos", {})

    return render_template("tos.html", **meta)