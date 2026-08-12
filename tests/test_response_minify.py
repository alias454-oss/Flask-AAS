# tests/test_response_minify.py
from unittest.mock import patch

from flask import Response

from app import create_app
from app.core.config import settings


def _response_document():
    return Response(
        """<!DOCTYPE html>
<html>
<head>
  <title>Response Contract</title>
  <!-- ordinary template comment -->
</head>
<body>
  <p>Response body</p>
</body>
</html>
""",
        content_type="text/html; charset=utf-8",
    )


def test_html_minification_preserves_document_structure_and_pagegen_marker():
    with patch.object(settings, "SQLALCHEMY_DATABASE_URI", "sqlite://"):
        app = create_app()

    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    app.add_url_rule("/_response-contract", view_func=_response_document)

    with patch("app.visitor_tracking_enabled", return_value=False):
        response = app.test_client().get("/_response-contract")

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<html>" in html
    assert "<head>" in html
    assert "</head>" in html
    assert "<body>" in html
    assert "</body>" in html
    assert "</html>" in html
    assert "</p>" in html
    assert "<!-- PageGen in " in html
    assert " ms -->" in html
    assert "ordinary template comment" not in html
