"""Connection pool for the central metadata catalog database."""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def get_catalog_engine() -> Engine:
    global _engine
    if _engine is None:
        uri = os.getenv(
            "REMETABASE_CATALOG_URL",
            "postgresql+psycopg2://metabase_admin:metabase_password@localhost:5432/metabase_catalog",
        )
        _engine = create_engine(uri, pool_pre_ping=True)
    return _engine
