import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from sqlalchemy.exc import OperationalError

from app import update_log_level
from app.core.extensions import db
from app.core.schema import table_exists
from app.models import EnvSettings


class CoreSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(cls.temp_dir.name) / "core-schema-tests.db"

        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.session.remove()
        db.drop_all()
        EnvSettings._cached_instance = None

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        EnvSettings._cached_instance = None
        self.app_context.pop()

    def test_table_exists_reports_missing_and_present_tables(self):
        self.assertFalse(table_exists(EnvSettings.__tablename__))

        db.create_all()

        self.assertTrue(table_exists(EnvSettings.__tablename__))

    def test_table_exists_fails_closed_when_inspection_is_unavailable(self):
        with patch(
            "app.core.schema.inspect",
            side_effect=OperationalError("statement", {}, RuntimeError("offline")),
        ):
            self.assertFalse(table_exists(EnvSettings.__tablename__))

    def test_update_log_level_skips_settings_query_before_schema_exists(self):
        with patch("app.table_exists", return_value=False), patch(
            "app.safe_get_cached_env_settings"
        ) as get_settings:
            update_log_level()

        get_settings.assert_not_called()
