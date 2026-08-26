from flask import Blueprint, current_app

favicon_bp = Blueprint("favicon", __name__)


@favicon_bp.get("/favicon.ico")
def favicon():
    return current_app.send_static_file("favicon.ico")


@favicon_bp.get("/apple-touch-icon.png")
def apple_touch_icon():
    return current_app.send_static_file("apple-touch-icon.png")


@favicon_bp.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon_precomposed():
    return current_app.send_static_file("apple-touch-icon-precomposed.png")