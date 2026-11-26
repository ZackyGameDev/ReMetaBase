"""Connection checks and metadata sync endpoints."""

from sqlalchemy import text
from fastapi import APIRouter

from schemas import DatabaseTargetConfig, SyncRequest
from services.catalog_repository import fetch_catalog_state, save_sync_to_catalog
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


@router.post("/sync")
def sync_databases(payload: SyncRequest) -> dict[str, list[dict[str, str | None]]]:
    summary = []
    for target in payload.targets:
        if not target.selected:
            continue
        result = extract_database_metadata(target, payload.sample_limit)
        if result.status == "success":
            try:
                snapshot = save_sync_to_catalog(target, result)
            except Exception as exc:
                summary.append({"id": target.id, "label": target.label, "status": "error", "error": f"Catalog save failed: {exc}"})
                continue
            summary.append({"id": target.id, "label": target.label, "status": result.status,
                            "error": result.error, "changed": snapshot["changed"],
                            "version_no": snapshot["version_no"], "changes": snapshot["changes"]})
            continue
        summary.append({"id": target.id, "label": target.label, "status": result.status, "error": result.error})
    return {"synced": summary}


@router.get("/catalog")
def get_catalog() -> list[dict]:
    return fetch_catalog_state()
