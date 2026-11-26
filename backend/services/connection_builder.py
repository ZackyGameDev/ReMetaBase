"""Construct SQLAlchemy engines for supported database targets."""

from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from schemas import DatabaseTargetConfig


def build_engine(target: DatabaseTargetConfig) -> Engine:
    dialect = target.dialect.lower().strip()
    if dialect == "sqlite":
        database = Path(target.database).expanduser()
        return create_engine(f"sqlite:///{database}")

    user = quote_plus(target.username or "")
    password = quote_plus(target.password or "")
    host = target.host or "localhost"
    if dialect == "postgresql":
        port = target.port or 5432
        uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{target.database}"
    elif dialect == "mysql":
        port = target.port or 3306
        uri = f"mysql+pymysql://{user}:{password}@{host}:{port}/{target.database}"
    else:
        raise ValueError(f"Unsupported dialect: {target.dialect}")

    return create_engine(uri, connect_args={"connect_timeout": 5})
