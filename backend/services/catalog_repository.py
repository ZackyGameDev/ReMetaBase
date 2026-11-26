"""Persist and read metadata catalog state."""

import json
from typing import Any

from sqlalchemy import text

from schemas import DatabaseSyncResult, DatabaseTargetConfig
from services.catalog_db import get_catalog_engine
from services.snapshot_manager import (
    commit_snapshot_if_changed,
    get_source_snapshot_history,
    load_catalog_state_for_diff,
)


def save_sync_to_catalog(target: DatabaseTargetConfig, result: DatabaseSyncResult) -> dict[str, Any]:
    if result.status != "success":
        return {"changed": False, "version_no": 0, "checksum": "", "changes": []}
    with get_catalog_engine().begin() as conn:
        source_row = conn.execute(text("""SELECT source_id FROM data_source WHERE db_type=:dialect
            AND COALESCE(host,'')=:host AND COALESCE(port,0)=:port AND database_name=:database"""),
            {"dialect": target.dialect, "host": target.host or "", "port": target.port or 0,
             "database": target.database}).mappings().first()
        if source_row:
            source_id = source_row["source_id"]
            conn.execute(text("UPDATE data_source SET name=:name,username=:username,status='ACTIVE',last_extracted_at=NOW() WHERE source_id=:id"),
                         {"name": target.label, "username": target.username, "id": source_id})
        else:
            source_id = conn.execute(text("""INSERT INTO data_source(name,db_type,host,port,database_name,username,status,last_extracted_at)
                VALUES (:name,:dialect,:host,:port,:database,:username,'ACTIVE',NOW()) RETURNING source_id"""),
                {"name": target.label, "dialect": target.dialect, "host": target.host, "port": target.port,
                 "database": target.database, "username": target.username}).scalar()
        schema_id = conn.execute(text("""INSERT INTO schema_meta(source_id,schema_name) VALUES (:source,:name)
            ON CONFLICT(source_id,schema_name) DO UPDATE SET schema_name=EXCLUDED.schema_name RETURNING schema_id"""),
            {"source": source_id, "name": result.schema_name}).scalar()
        previous = load_catalog_state_for_diff(conn, schema_id)
        table_ids: dict[str, int] = {}
        column_ids: dict[str, dict[str, int]] = {}
        for table in result.tables:
            table_id = conn.execute(text("""INSERT INTO table_meta(schema_id,table_name,is_active) VALUES (:schema,:name,TRUE)
                ON CONFLICT(schema_id,table_name) DO UPDATE SET is_active=TRUE RETURNING table_id"""),
                {"schema": schema_id, "name": table.table_name}).scalar()
            table_ids[table.table_name] = table_id
            column_ids[table.table_name] = {}
            for col in table.columns:
                mapping_id = conn.execute(text("""INSERT INTO type_mapping(dialect,native_type,unified_type) VALUES (:dialect,:native,:unified)
                    ON CONFLICT(dialect,native_type) DO UPDATE SET unified_type=EXCLUDED.unified_type RETURNING type_mapping_id"""),
                    {"dialect": target.dialect, "native": col.raw_type[:100], "unified": col.unified_type[:50]}).scalar()
                props = {**col.dialect_extras, "default": col.default, "unique": col.unique,
                         "check_constraints": col.check_constraints, "null_fraction": col.null_fraction}
                column_id = conn.execute(text("""INSERT INTO column_meta(table_id,type_mapping_id,column_name,raw_type,ordinal_position,is_primary_key,is_nullable,is_active,dialect_props)
                    VALUES (:table,:mapping,:name,:raw,:position,:pk,:nullable,TRUE,CAST(:props AS JSONB))
                    ON CONFLICT(table_id,column_name) DO UPDATE SET type_mapping_id=EXCLUDED.type_mapping_id,raw_type=EXCLUDED.raw_type,
                    ordinal_position=EXCLUDED.ordinal_position,is_primary_key=EXCLUDED.is_primary_key,is_nullable=EXCLUDED.is_nullable,
                    is_active=TRUE,dialect_props=EXCLUDED.dialect_props RETURNING column_id"""),
                    {"table": table_id, "mapping": mapping_id, "name": col.name, "raw": col.raw_type[:100],
                     "position": col.ordinal_position, "pk": col.is_primary_key, "nullable": col.nullable,
                     "props": json.dumps(props, default=str)}).scalar()
                column_ids[table.table_name][col.name] = column_id
            last_metric = conn.execute(text("""SELECT row_count,null_fraction FROM operational_metric
                WHERE table_id=:id ORDER BY profiled_at DESC LIMIT 1"""), {"id": table_id}).mappings().first()
            new_fraction = round(float(table.null_fraction), 6)
            if (last_metric is None or int(last_metric["row_count"]) != int(table.row_count)
                    or round(float(last_metric["null_fraction"]), 6) != new_fraction):
                conn.execute(text("INSERT INTO operational_metric(table_id,row_count,null_fraction) VALUES (:id,:count,:fraction)"),
                             {"id": table_id, "count": table.row_count, "fraction": new_fraction})

        # Reconcile FKs after every table/column exists. Delete stale outgoing refs first.
        current_column_ids = [cid for ids in column_ids.values() for cid in ids.values()]
        for ids in column_ids.values():
            for cid in ids.values():
                conn.execute(text("DELETE FROM column_references WHERE child_column_id=:id"), {"id": cid})
        for table in result.tables:
            for col in table.columns:
                if not col.foreign_key or "." not in col.foreign_key:
                    continue
                ref_table, ref_col = col.foreign_key.split(".", 1)
                child_id = column_ids[table.table_name].get(col.name)
                parent_id = column_ids.get(ref_table, {}).get(ref_col)
                if child_id and parent_id:
                    conn.execute(text("INSERT INTO column_references(child_column_id,parent_column_id) VALUES (:child,:parent) ON CONFLICT DO NOTHING"),
                                 {"child": child_id, "parent": parent_id})

        for table in result.tables:
            table_id = table_ids[table.table_name]
            seen_indexes = set()
            for index in table.indexes:
                name = index.index_name[:150]
                seen_indexes.add(name)
                index_id = conn.execute(text("""INSERT INTO index_meta(table_id,index_name,is_unique) VALUES (:table,:name,:unique)
                    ON CONFLICT(table_id,index_name) DO UPDATE SET is_unique=EXCLUDED.is_unique RETURNING index_id"""),
                    {"table": table_id, "name": name, "unique": index.is_unique}).scalar()
                conn.execute(text("DELETE FROM index_columns WHERE index_id=:id"), {"id": index_id})
                for position, name_col in enumerate(index.columns, 1):
                    column_id = column_ids[table.table_name].get(name_col)
                    if column_id:
                        conn.execute(text("INSERT INTO index_columns(index_id,column_id,position) VALUES (:index,:column,:position) ON CONFLICT DO NOTHING"),
                                     {"index": index_id, "column": column_id, "position": position})
            existing = conn.execute(text("SELECT index_id,index_name FROM index_meta WHERE table_id=:table"), {"table": table_id}).mappings().all()
            for old in existing:
                if old["index_name"] not in seen_indexes:
                    conn.execute(text("DELETE FROM index_meta WHERE index_id=:id"), {"id": old["index_id"]})

        for old_table in previous.values():
            if old_table["table_id"] not in table_ids.values():
                conn.execute(text("UPDATE table_meta SET is_active=FALSE WHERE table_id=:id"), {"id": old_table["table_id"]})
        for table in result.tables:
            present = {col.name for col in table.columns}
            for old_col in previous.get(table.table_name, {}).get("columns", {}).values():
                if old_col["column_id"] not in column_ids[table.table_name].values():
                    conn.execute(text("UPDATE column_meta SET is_active=FALSE WHERE column_id=:id"), {"id": old_col["column_id"]})

        snapshot = commit_snapshot_if_changed(conn, source_id, result, previous)
        return snapshot


def fetch_catalog_state() -> list[dict[str, Any]]:
    with get_catalog_engine().connect() as conn:
        sources = conn.execute(text("SELECT source_id,name,db_type,last_extracted_at FROM data_source ORDER BY source_id")).mappings().all()
        output = []
        for source in sources:
            tables = conn.execute(text("""SELECT t.table_id,t.table_name,COALESCE(m.row_count,0) row_count,COALESCE(m.null_fraction,0) null_fraction
                FROM table_meta t JOIN schema_meta s ON s.schema_id=t.schema_id
                LEFT JOIN LATERAL (SELECT row_count,null_fraction FROM operational_metric WHERE table_id=t.table_id ORDER BY profiled_at DESC LIMIT 1) m ON TRUE
                WHERE s.source_id=:source AND t.is_active ORDER BY t.table_name"""), {"source": source["source_id"]}).mappings().all()
            table_list = []
            for table in tables:
                columns = conn.execute(text("""SELECT c.column_id,c.column_name,c.raw_type,tm.unified_type,c.is_primary_key,c.is_nullable,c.ordinal_position,c.dialect_props,
                    pt.table_name || '.' || pc.column_name AS foreign_key FROM column_meta c
                    LEFT JOIN type_mapping tm ON tm.type_mapping_id=c.type_mapping_id
                    LEFT JOIN column_references cr ON cr.child_column_id=c.column_id LEFT JOIN column_meta pc ON pc.column_id=cr.parent_column_id
                    LEFT JOIN table_meta pt ON pt.table_id=pc.table_id WHERE c.table_id=:table AND c.is_active ORDER BY c.ordinal_position"""),
                    {"table": table["table_id"]}).mappings().all()
                table_list.append({**dict(table), "columns": [dict(column) for column in columns]})
            latest = conn.execute(text("SELECT version_no FROM schema_snapshot WHERE source_id=:source ORDER BY version_no DESC LIMIT 1"), {"source": source["source_id"]}).scalar()
            output.append({"source_id": source["source_id"], "name": source["name"], "db_type": source["db_type"],
                           "last_extracted_at": str(source["last_extracted_at"]), "current_version": latest or 0,
                           "snapshot_history": get_source_snapshot_history(conn, source["source_id"]), "tables": table_list})
        return output
