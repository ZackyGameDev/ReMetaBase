"""FastAPI application entry point for ReMetaBase."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers.inspect_router import router as inspect_router
from routers.snapshot_router import router as snapshot_router
from services.auto_sync import auto_sync_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto_sync_manager.start()
    yield
    await auto_sync_manager.stop()


app = FastAPI(title="ReMetaBase Metadata Engine", version="0.2.0", lifespan=lifespan)
app.include_router(inspect_router)
app.include_router(snapshot_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
