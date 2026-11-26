"""Connection checks and metadata sync endpoints."""

from sqlalchemy import text
from fastapi import APIRouter

from schemas import DatabaseSyncResult, DatabaseTargetConfig, SyncRequest
from services.connection_builder import build_engine
from services.metadata_extractor import extract_database_metadata

router = APIRouter(prefix="/api", tags=["Metadata Inspection"])


@router.post("/test-connection")
def test_connection(target: DatabaseTargetConfig) -> dict[str, str | bool]:
    engine = None
    try:
        engine = build_engine(target)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "message": f"Connected to {target.label} ({target.dialect})"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        if engine is not None:
            engine.dispose()


@router.post("/sync", response_model=list[DatabaseSyncResult])
def sync_databases(payload: SyncRequest) -> list[DatabaseSyncResult]:
    return [
        extract_database_metadata(target, payload.sample_limit)
        for target in payload.targets if target.selected
    ]
