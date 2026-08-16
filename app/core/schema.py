# app/core/schema.py
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.core.extensions import db


def table_exists(table_name: str) -> bool:
    """Return whether a table exists in the configured application database.

    This is intentionally a small host-level readiness primitive. Callers that
    depend on DB-backed configuration can use it during greenfield bootstrap to
    avoid issuing ORM queries against tables that migrations have not created
    yet. Database connectivity or inspection failures fail closed as ``False``.
    """

    try:
        return inspect(db.engine).has_table(table_name)
    except SQLAlchemyError:
        return False
