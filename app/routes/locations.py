# app/routes/locations.py
from flask import Blueprint, jsonify, request

from app.core.extensions import limiter
from app.core.locations import normalize_country_code, zone_records
from app.core.security import get_client_ip

locations_bp = Blueprint("locations", __name__, url_prefix="/reference")


@locations_bp.route("/zones", methods=["GET"])
@limiter.limit("60 per minute", key_func=get_client_ip)
def zones():
    country_code = normalize_country_code(request.args.get("country"))
    if not country_code or len(country_code) != 2:
        return jsonify({"country": country_code, "zones": []})

    return jsonify({"country": country_code, "zones": zone_records(country_code)})
