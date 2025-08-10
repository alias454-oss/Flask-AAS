# routes/about.py
import logging
from flask import Blueprint, render_template
from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

about_bp = Blueprint('about', __name__)

@about_bp.route('/about')
@log_view_action()
def about():
    meta = page_metadata.get("about", {})

    return render_template('about.html', **meta)
