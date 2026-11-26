"""Extract schema, lightweight profile metrics, and bounded sample rows."""

from typing import Any

from sqlalchemy import inspect, text

from schemas import (ColumnMetadata, DatabaseSyncResult, DatabaseTargetConfig,
                     IndexMetadata, TableMetadata)
from services.connection_builder import build_engine
from services.type_normalizer import resolve_unified_type


def _clean_value(value: Any) -> Any:
    return value if value is None or isinstance(value, (int, float, bool, str)) else str(value)


def extract_database_metadata(target: DatabaseTargetConfig, sample_limit: int = 5) -> DatabaseSyncResult:
    engine = None
    try:
        engine = build_engine(target)
        inspector = inspect(engine)
        tables: list[TableMetadata] = []
        with engine.connect() as conn:
            for table_name in inspector.get_table_names():
                raw_columns = inspector.get_columns(table_name)
                pk = set((inspector.get_pk_constraint(table_name) or {}).get("constrained_columns") or [])
                fks: dict[str, str] = {}
                for fk in inspector.get_foreign_keys(table_name):
                    ref_cols = fk.get("referred_columns") or []
                    for i, name in enumerate(fk.get("constrained_columns") or []):
                        fks[name] = f"{fk.get('referred_table')}.{ref_cols[i] if i < len(ref_cols) else ''}"
                uniques: set[str] = set()
                for uq in inspector.get_unique_constraints(table_name):
                    uniques.update(uq.get("column_names") or [])
                indexes = []
                for item in inspector.get_indexes(table_name):
                    names = [c for c in (item.get("column_names") or []) if c]
                    if item.get("unique"):
                        uniques.update(names)
                    indexes.append(IndexMetadata(index_name=item.get("name") or "unnamed",
                                                 is_unique=bool(item.get("unique")), columns=names))
                checks = inspector.get_check_constraints(table_name) or []
                quote = engine.dialect.identifier_preparer.quote
                table_sql = quote(table_name)
                null_exprs = ", ".join(
                    f"SUM(CASE WHEN {quote(col['name'])} IS NULL THEN 1 ELSE 0 END)"
                    for col in raw_columns
                )
                stats = conn.execute(text(f"SELECT COUNT(*){', ' + null_exprs if null_exprs else ''} FROM {table_sql}")).fetchone()
                row_count = int(stats[0] or 0)
                columns = []
                for pos, col in enumerate(raw_columns, start=1):
                    name = col['name']
                    null_fraction = round(int(stats[pos] or 0) / row_count, 4) if row_count else 0.0
                    matching_checks = [c.get('sqltext', '') for c in checks if name in (c.get('sqltext') or '')]
                    unique = name in uniques or name in pk
                    default = str(col['default']) if col.get('default') is not None else None
                    extras = {'default': default, 'unique': unique, 'check_constraints': matching_checks,
                              'autoincrement': _clean_value(col.get('autoincrement')),
                              'comment': col.get('comment'), 'null_fraction': null_fraction}
                    columns.append(ColumnMetadata(name=name, ordinal_position=pos,
                        raw_type=str(col['type']), unified_type=resolve_unified_type(col['type']),
                        nullable=bool(col.get('nullable', True)), default=default,
                        is_primary_key=name in pk, foreign_key=fks.get(name), unique=unique,
                        check_constraints=matching_checks, null_fraction=null_fraction,
                        dialect_extras=extras))
                avg_null = round(sum(c.null_fraction for c in columns) / len(columns), 4) if columns else 0.0
                rows = conn.execute(text(f"SELECT * FROM {table_sql} LIMIT :lim"), {'lim': sample_limit}).mappings().all()
                samples = [{k: _clean_value(v) for k, v in row.items()} for row in rows]
                tables.append(TableMetadata(table_name=table_name, row_count=row_count,
                    null_fraction=avg_null, columns=columns, indexes=indexes, sample_rows=samples))
        return DatabaseSyncResult(id=target.id, label=target.label, dialect=target.dialect,
            schema_name=inspector.default_schema_name or target.database, status='success', tables=tables)
    except Exception as exc:
        return DatabaseSyncResult(id=target.id, label=target.label, dialect=target.dialect,
                                  status='error', error=str(exc))
    finally:
        if engine is not None:
            engine.dispose()
