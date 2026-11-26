"""FastAPI application entry point for ReMetaBase."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routers.inspect_router import router as inspect_router
from routers.snapshot_router import router as snapshot_router
from routers.annotation_router import router as annotation_router
from routers.lineage_router import router as lineage_router
from routers.search_router import router as search_router
from services.auto_sync import auto_sync_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto_sync_manager.start()
    yield
    await auto_sync_manager.stop()


app = FastAPI(title="ReMetaBase Metadata Engine", version="0.2.0", lifespan=lifespan)
app.include_router(inspect_router)
app.include_router(snapshot_router)
app.include_router(annotation_router)
app.include_router(lineage_router)
app.include_router(search_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
