"""Explicit schema change sync and snapshot history endpoints."""

from fastapi import APIRouter

from schemas import SyncRequest
from services.catalog_repository import save_sync_to_catalog
from services.catalog_db import get_catalog_engine
from services.metadata_extractor import extract_database_metadata
from services.snapshot_manager import get_source_snapshot_history
from sqlalchemy import text

router = APIRouter(prefix="/api/snapshots", tags=["Schema Version Control"])


@router.post("/sync-changes")
def sync_and_commit_changes(payload: SyncRequest) -> dict:
    reports = []
    for target in payload.targets:
        if not target.selected:
            continue
        result = extract_database_metadata(target, payload.sample_limit)
        if result.status != "success":
            reports.append({"id": target.id, "label": target.label, "status": "error", "error": result.error})
            continue
        try:
            snapshot = save_sync_to_catalog(target, result)
            reports.append({"id": target.id, "label": target.label, "status": "success", **snapshot})
        except Exception as exc:
            reports.append({"id": target.id, "label": target.label, "status": "error", "error": str(exc)})
    return {"reports": reports}


@router.get("/{source_id}")
def get_snapshot_history(source_id: int) -> list[dict]:
    with get_catalog_engine().connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM data_source WHERE source_id=:id"), {"id": source_id}).scalar()
        if not exists:
            return []
        return get_source_snapshot_history(conn, source_id)
