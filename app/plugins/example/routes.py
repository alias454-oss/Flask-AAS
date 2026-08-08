"""Web surface for the Flask-AAS Plugin API reference application."""

from flask import Blueprint, render_template

from app.plugins.interface import PLUGIN_API_VERSION


example_bp = Blueprint(
    "example",
    __name__,
    url_prefix="/example",
    template_folder="templates",
    static_folder="static",
)


@example_bp.get("/")
def index():
    """Render the minimal reference-plugin application page."""

    return render_template(
        "example/index.html",
        title="Example Application",
        plugin_id="example",
        plugin_api_version=PLUGIN_API_VERSION,
    )
