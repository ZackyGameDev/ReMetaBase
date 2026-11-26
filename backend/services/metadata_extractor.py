"""Read schema details and a bounded sample of rows from a target database."""

from typing import Any

from sqlalchemy import inspect, text

from schemas import (
    ColumnMetadata,
    DatabaseSyncResult,
    DatabaseTargetConfig,
    TableMetadata,
)
from services.connection_builder import build_engine


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def extract_database_metadata(
    target: DatabaseTargetConfig, sample_limit: int = 5
) -> DatabaseSyncResult:
    engine = None
    try:
        engine = build_engine(target)
        inspector = inspect(engine)
        tables: list[TableMetadata] = []
        with engine.connect() as conn:
            for table_name in inspector.get_table_names():
                pk = inspector.get_pk_constraint(table_name) or {}
                pk_columns = set(pk.get("constrained_columns") or [])
                foreign_keys: dict[str, str] = {}
                for fk in inspector.get_foreign_keys(table_name):
                    for index, column in enumerate(fk.get("constrained_columns") or []):
                        referred = fk.get("referred_columns") or []
                        ref_column = referred[index] if index < len(referred) else ""
                        foreign_keys[column] = f"{fk.get('referred_table')}.{ref_column}"

                unique_columns: set[str] = set()
                for unique in inspector.get_unique_constraints(table_name):
                    unique_columns.update(unique.get("column_names") or [])
                for index in inspector.get_indexes(table_name):
                    if index.get("unique"):
                        unique_columns.update(index.get("column_names") or [])

                checks = inspector.get_check_constraints(table_name) or []
                columns = []
                for column in inspector.get_columns(table_name):
                    name = column["name"]
                    matching_checks = [
                        item.get("sqltext", "") for item in checks
                        if name in (item.get("sqltext") or "")
                    ]
                    columns.append(ColumnMetadata(
                        name=name, raw_type=str(column["type"]),
                        nullable=bool(column.get("nullable", True)),
                        default=str(column["default"]) if column.get("default") is not None else None,
                        is_primary_key=name in pk_columns,
                        foreign_key=foreign_keys.get(name),
                        unique=name in unique_columns or name in pk_columns,
                        check_constraints=matching_checks,
                    ))

                quoted = engine.dialect.identifier_preparer.quote(table_name)
                count = conn.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar() or 0
                rows = conn.execute(
                    text(f"SELECT * FROM {quoted} LIMIT :limit"), {"limit": sample_limit}
                ).mappings().all()
                samples = [{key: _serialize_value(value) for key, value in row.items()} for row in rows]
                tables.append(TableMetadata(
                    table_name=table_name, row_count=count, columns=columns, sample_rows=samples
                ))
        return DatabaseSyncResult(
            id=target.id, label=target.label, dialect=target.dialect,
            status="success", tables=tables,
        )
    except Exception as exc:
        return DatabaseSyncResult(
            id=target.id, label=target.label, dialect=target.dialect,
            status="error", error=str(exc),
        )
    finally:
        if engine is not None:
            engine.dispose()
