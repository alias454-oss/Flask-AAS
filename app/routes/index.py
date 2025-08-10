# routes/index.py
import logging
from flask import Blueprint, render_template
from app.core.meta import page_metadata
from app.core.decorators import log_view_action

logger = logging.getLogger(__name__)

index_bp = Blueprint('index', __name__)

@index_bp.route('/')
@log_view_action()
def index():
    meta = page_metadata.get("index", {})

    return render_template("index.html", **meta)