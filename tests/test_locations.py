import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.core.extensions import db, limiter
from app.core.locations import country_choices, zone_choices, zone_records
from app.core.seeder import seed_countries, seed_zones
from app.models import Country, Zone
from app.routes.locations import locations_bp


class LocationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "location-reference-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="location-reference-test-secret",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            RATELIMIT_ENABLED=False,
        )
        db.init_app(cls.app)
        limiter.init_app(cls.app)
        cls.app.register_blueprint(locations_bp)

        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        seed_countries()
        seed_zones()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        cls.app_context.pop()
        cls.temp_dir.cleanup()

    def test_iso_catalog_is_fully_seeded_without_legacy_state_table(self):
        app_data = Path(__file__).resolve().parents[1] / "app" / "data"
        country_source = json.loads((app_data / "iso_3166-1.json").read_text(encoding="utf-8"))
        zone_source = json.loads((app_data / "iso_3166-2.json").read_text(encoding="utf-8"))

        self.assertEqual(Country.query.count(), len(country_source["3166-1"]))
        self.assertEqual(Zone.query.count(), len(zone_source["3166-2"]))
        self.assertNotIn("states", db.metadata.tables)

    def test_zone_codes_and_hierarchy_preserve_iso_identity(self):
        illinois = Zone.query.filter_by(code="US-IL").one()
        ontario = Zone.query.filter_by(code="CA-ON").one()
        england = Zone.query.filter_by(code="GB-ENG").one()
        wiltshire = Zone.query.filter_by(code="GB-WIL").one()

        self.assertEqual(illinois.name, "Illinois")
        self.assertEqual(illinois.type, "State")
        self.assertIsNone(illinois.parent_zone_id)
        self.assertEqual(ontario.type, "Province")
        self.assertEqual(wiltshire.parent_zone_id, england.zone_id)
        self.assertEqual(wiltshire.country.iso_code_2, "GB")

    def test_location_choices_are_host_driven_and_hierarchy_aware(self):
        countries = dict(country_choices())
        canada = dict(zone_choices("CA"))
        united_kingdom = dict(zone_choices("GB"))

        self.assertEqual(countries["US"], "United States")
        self.assertEqual(canada["CA-ON"], "Ontario")
        self.assertEqual(united_kingdom["GB-WIL"], "England — Wiltshire")

    def test_public_zone_endpoint_returns_generic_reference_data(self):
        response = self.client.get("/reference/zones?country=CA")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["country"], "CA")
        by_code = {zone["code"]: zone for zone in payload["zones"]}
        self.assertEqual(by_code["CA-ON"]["name"], "Ontario")
        self.assertEqual(by_code["CA-ON"]["type"], "Province")
        self.assertIsNone(by_code["CA-ON"]["parent"])

    def test_seeders_are_repeatable(self):
        country_count = Country.query.count()
        zone_count = Zone.query.count()

        seed_countries()
        seed_zones()

        self.assertEqual(Country.query.count(), country_count)
        self.assertEqual(Zone.query.count(), zone_count)

    def test_country_without_iso_subdivisions_has_blank_zone_choice(self):
        self.assertEqual(zone_records("AQ"), [])
        self.assertEqual(zone_choices("AQ"), [("", "No ISO subdivision available")])
