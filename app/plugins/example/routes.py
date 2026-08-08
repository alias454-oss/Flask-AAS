"""Web surface for the Flask-AAS Plugin API reference application."""

from flask import Blueprint, render_template
from sqlalchemy import func, select

from app.core.extensions import db
from app.plugins.example.models import ExampleItem, get_example_settings
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

    settings = get_example_settings()
    item_count = db.session.scalar(select(func.count(ExampleItem.id))) or 0

    return render_template(
        "example/index.html",
        title="Example Application",
        plugin_id="example",
        plugin_api_version=PLUGIN_API_VERSION,
        greeting=settings.greeting if settings is not None else "",
        item_count=item_count,
    )
