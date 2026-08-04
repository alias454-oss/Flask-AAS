from flask import Blueprint, current_app

favicon_bp = Blueprint("favicon", __name__)


@favicon_bp.get("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.ico")