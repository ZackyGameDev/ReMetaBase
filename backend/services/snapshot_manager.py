"""Canonical schema fingerprints, diffs, and snapshot history."""

import hashlib
import json
from typing import Any

from sqlalchemy import Connection, text

from schemas import DatabaseSyncResult


def _props(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extracted_spec(result: DatabaseSyncResult) -> dict[str, Any]:
    tables = {}
    for table in sorted(result.tables, key=lambda item: item.table_name):
        columns = {}
        for col in sorted(table.columns, key=lambda item: item.ordinal_position):
            columns[col.name] = {
                "ordinal_position": col.ordinal_position, "raw_type": col.raw_type,
                "unified_type": col.unified_type, "nullable": bool(col.nullable),
                "primary_key": bool(col.is_primary_key), "foreign_key": col.foreign_key,
                "unique": bool(col.unique), "default": col.default,
                "checks": sorted(col.check_constraints or []),
            }
        indexes = {idx.index_name: {"unique": bool(idx.is_unique), "columns": list(idx.columns)}
                   for idx in sorted(table.indexes, key=lambda item: item.index_name)}
        tables[table.table_name] = {"columns": columns, "indexes": indexes}
    return {"schema_name": result.schema_name, "tables": tables}


def compute_schema_checksum(result: DatabaseSyncResult) -> str:
    encoded = json.dumps(extracted_spec(result), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_catalog_state_for_diff(conn: Connection, schema_id: int) -> dict[str, Any]:
    rows = conn.execute(text("""SELECT t.table_id,t.table_name,t.is_active table_active,
        c.column_id,c.column_name,c.ordinal_position,c.raw_type,tm.unified_type,c.is_nullable,c.is_primary_key,c.is_active column_active,c.dialect_props,
        pt.table_name || '.' || pc.column_name foreign_key
        FROM table_meta t LEFT JOIN column_meta c ON c.table_id=t.table_id
        LEFT JOIN type_mapping tm ON tm.type_mapping_id=c.type_mapping_id
        LEFT JOIN column_references cr ON cr.child_column_id=c.column_id
        LEFT JOIN column_meta pc ON pc.column_id=cr.parent_column_id
        LEFT JOIN table_meta pt ON pt.table_id=pc.table_id
        WHERE t.schema_id=:schema ORDER BY t.table_name,c.ordinal_position"""), {"schema": schema_id}).mappings().all()
    state: dict[str, Any] = {}
    for row in rows:
        name = row["table_name"]
        table = state.setdefault(name, {"table_id": row["table_id"], "is_active": bool(row["table_active"]), "columns": {}, "indexes": {}})
        if row["column_id"] is not None:
            props = _props(row["dialect_props"])
            table["columns"][row["column_name"]] = {
                "column_id": row["column_id"],
                "is_active": bool(row["column_active"]),
                "spec": {"ordinal_position": row["ordinal_position"], "raw_type": row["raw_type"],
                         "unified_type": row["unified_type"], "nullable": bool(row["is_nullable"]),
                         "primary_key": bool(row["is_primary_key"]), "foreign_key": row["foreign_key"],
                         "unique": bool(props.get("unique", row["is_primary_key"])),
                         "default": props.get("default"), "checks": sorted(props.get("check_constraints") or []),
        },
            }
    index_rows = conn.execute(text("""SELECT im.table_id,im.index_name,im.is_unique,cm.column_name
        FROM index_meta im LEFT JOIN index_columns ic ON ic.index_id=im.index_id
        LEFT JOIN column_meta cm ON cm.column_id=ic.column_id ORDER BY im.index_name,ic.position""")).mappings().all()
    by_id = {value["table_id"]: value for value in state.values()}
    for row in index_rows:
        table = by_id.get(row["table_id"])
        if table is None:
            continue
        index = table["indexes"].setdefault(row["index_name"], {"unique": bool(row["is_unique"]), "columns": []})
        if row["column_name"]:
            index["columns"].append(row["column_name"])
    return state


def detect_schema_changes(previous: dict[str, Any], result: DatabaseSyncResult) -> list[dict[str, Any]]:
    current = extracted_spec(result)["tables"]
    changes: list[dict[str, Any]] = []
    encode = lambda value: json.dumps(value, sort_keys=True, default=str) if value is not None else None
    for name, spec in current.items():
        old = previous.get(name)
        if old is None:
            changes.append({"type": "TABLE", "name": name, "action": "ADDED", "old": None, "new": encode(spec)})
            continue
        if not old["is_active"]:
            changes.append({"type": "TABLE", "name": name, "action": "RESTORED", "old": None, "new": encode(spec)})
        for col, col_spec in spec["columns"].items():
            prior = old["columns"].get(col)
            fq = f"{name}.{col}"
            if prior is None or not prior["is_active"]:
                changes.append({"type": "COLUMN", "name": fq, "action": "ADDED" if prior is None else "RESTORED", "old": None, "new": encode(col_spec)})
            elif prior["spec"] != col_spec:
                changes.append({"type": "COLUMN", "name": fq, "action": "MODIFIED", "old": encode(prior["spec"]), "new": encode(col_spec)})
        for col, prior in old["columns"].items():
            if col not in spec["columns"] and prior["is_active"]:
                changes.append({"type": "COLUMN", "name": f"{name}.{col}", "action": "DROPPED", "old": encode(prior["spec"]), "new": None})
        for idx, idx_spec in spec["indexes"].items():
            prior = old["indexes"].get(idx)
            fq = f"{name}.{idx}"
            action = "ADDED" if prior is None else "MODIFIED" if prior != idx_spec else None
            if action:
                changes.append({"type": "INDEX", "name": fq, "action": action, "old": encode(prior), "new": encode(idx_spec)})
        for idx, prior in old["indexes"].items():
            if idx not in spec["indexes"]:
                changes.append({"type": "INDEX", "name": f"{name}.{idx}", "action": "DROPPED", "old": encode(prior), "new": None})
    for name, old in previous.items():
        if name not in current and old["is_active"]:
            bundle = {"columns": {k: v["spec"] for k, v in old["columns"].items() if v["is_active"]}, "indexes": old["indexes"]}
            changes.append({"type": "TABLE", "name": name, "action": "DROPPED", "old": encode(bundle), "new": None})
    return changes


def commit_snapshot_if_changed(conn: Connection, source_id: int, result: DatabaseSyncResult,
                               previous: dict[str, Any]) -> dict[str, Any]:
    checksum = compute_schema_checksum(result)
    latest = conn.execute(text("SELECT snapshot_id,version_no,checksum,captured_at FROM schema_snapshot WHERE source_id=:id ORDER BY version_no DESC LIMIT 1"), {"id": source_id}).mappings().first()
    if latest and latest["checksum"] == checksum:
        return {"changed": False, "version_no": latest["version_no"], "checksum": checksum[:12], "changes": []}
    version = 1 if latest is None else latest["version_no"] + 1
    changes = detect_schema_changes(previous, result)
    snapshot_id = conn.execute(text("INSERT INTO schema_snapshot(source_id,version_no,checksum) VALUES (:source,:version,:checksum) RETURNING snapshot_id"),
                               {"source": source_id, "version": version, "checksum": checksum}).scalar()
    for change in changes:
        conn.execute(text("INSERT INTO schema_change_log(snapshot_id,object_type,object_name,change_type,old_value,new_value) VALUES (:snapshot,:type,:name,:action,:old,:new)"),
                     {"snapshot": snapshot_id, **change})
    return {"changed": True, "version_no": version, "checksum": checksum[:12], "changes": changes}


def get_source_snapshot_history(conn: Connection, source_id: int) -> list[dict[str, Any]]:
    snapshots = conn.execute(text("SELECT snapshot_id,version_no,checksum,captured_at FROM schema_snapshot WHERE source_id=:id ORDER BY version_no DESC"), {"id": source_id}).mappings().all()
    history = []
    for snapshot in snapshots:
        changes = conn.execute(text("SELECT change_id,object_type,object_name,change_type,old_value,new_value FROM schema_change_log WHERE snapshot_id=:id ORDER BY change_id"), {"id": snapshot["snapshot_id"]}).mappings().all()
        history.append({"snapshot_id": snapshot["snapshot_id"], "version_no": snapshot["version_no"],
                        "checksum": snapshot["checksum"][:12], "captured_at": str(snapshot["captured_at"]),
                        "changes": [dict(change) for change in changes]})
    return history
