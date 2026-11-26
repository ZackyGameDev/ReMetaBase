"""Normalize SQLAlchemy types without dialect-specific lookup tables."""

from sqlalchemy.sql import sqltypes
from sqlalchemy.types import TypeEngine


def resolve_unified_type(sa_type: TypeEngine) -> str:
    try:
        generic = sa_type.as_generic()
    except Exception:
        return type(sa_type).__name__.upper()
    if isinstance(generic, sqltypes.Enum):
        return "ENUM"
    if isinstance(generic, sqltypes.JSON):
        return "JSON"
    try:
        python_type = generic.python_type
        mapping = {int: "INTEGER", str: "STRING", float: "FLOAT",
                   bool: "BOOLEAN", bytes: "BINARY"}
        return mapping.get(python_type, python_type.__name__.upper())
    except (NotImplementedError, AttributeError):
        return type(generic).__name__.upper()
