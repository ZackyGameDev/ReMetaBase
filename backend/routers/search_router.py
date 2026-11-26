"""Natural-language catalog search endpoint."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.catalog_search import search_catalog

router = APIRouter(prefix="/api/search", tags=["Catalog Search"])


class CatalogQueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)


@router.post("")
async def ask_catalog(payload: CatalogQueryRequest) -> dict:
    return await asyncio.to_thread(search_catalog, payload.query)
