"""CRUD endpoints for table and column business annotations."""

import asyncio
import logging
import traceback
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from services.catalog_db import get_catalog_engine
from services.ai_annotator import generate_ai_annotation

router = APIRouter(prefix="/api/annotations", tags=["Business Annotations"])


class AnnotationUpsertRequest(BaseModel):
    target_type: Literal["TABLE", "COLUMN"]
    target_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    data_owner: str = ""
    definition: str = ""
    purpose: str = ""
    tags: list[str] = Field(default_factory=list)


class AiSuggestRequest(BaseModel):
    target_type: Literal["TABLE", "COLUMN"]
    target_id: int = Field(gt=0)


@router.post("/ai-suggest")
async def suggest_annotation_with_ai(payload: AiSuggestRequest) -> dict:
    try:
        return await asyncio.to_thread(generate_ai_annotation, payload.target_type, payload.target_id)
    except ValueError as exc:
        logging.exception("AI annotation target lookup/validation failed")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("AI annotation suggestion failed")
        print("AI annotation suggestion failed:", repr(exc), flush=True)
        traceback.print_exc()
        raise HTTPException(status_code=502,
            detail=f"AI suggestion failed ({type(exc).__name__}). Check the server log for details, or fill the fields manually.") from exc


@router.get("/users")
def list_app_users() -> dict:
    with get_catalog_engine().connect() as conn:
        rows = conn.execute(text("SELECT user_id,name,email,role FROM app_user ORDER BY user_id")).mappings().all()
        return {"users": [dict(row) for row in rows]}


@router.post("")
def upsert_annotation(payload: AnnotationUpsertRequest) -> dict:
    target_column = payload.target_type == "COLUMN"
    target_table = None if target_column else payload.target_id
    target_col = payload.target_id if target_column else None
    with get_catalog_engine().begin() as conn:
        valid_user = conn.execute(text("SELECT 1 FROM app_user WHERE user_id=:id"), {"id": payload.user_id}).scalar()
        if not valid_user:
            raise HTTPException(status_code=400, detail="Unknown user_id")
        valid_target = conn.execute(
            text("SELECT 1 FROM column_meta WHERE column_id=:id" if target_column else "SELECT 1 FROM table_meta WHERE table_id=:id"),
            {"id": payload.target_id},
        ).scalar()
        if not valid_target:
            raise HTTPException(status_code=404, detail="Annotation target not found")
        condition = "column_id=:target" if target_column else "table_id=:target"
        existing = conn.execute(text(f"SELECT annotation_id FROM business_annotation WHERE {condition} ORDER BY annotation_id LIMIT 1"),
                                {"target": payload.target_id}).scalar()
        params = {"table": target_table, "column": target_col, "user": payload.user_id,
                  "owner": payload.data_owner.strip() or None, "definition": payload.definition.strip() or None,
                  "purpose": payload.purpose.strip() or None,
                  "tags": __import__("json").dumps([tag.strip() for tag in payload.tags if tag.strip()])}
        if existing:
            conn.execute(text("""UPDATE business_annotation SET user_id=:user,data_owner=:owner,definition=:definition,
                purpose=:purpose,tags=CAST(:tags AS JSONB) WHERE annotation_id=:id"""), {**params, "id": existing})
            return {"status": "updated", "annotation_id": existing}
        annotation_id = conn.execute(text("""INSERT INTO business_annotation(table_id,column_id,user_id,data_owner,definition,purpose,tags)
            VALUES (:table,:column,:user,:owner,:definition,:purpose,CAST(:tags AS JSONB)) RETURNING annotation_id"""), params).scalar()
        return {"status": "created", "annotation_id": annotation_id}


@router.delete("/{annotation_id}")
def delete_annotation(annotation_id: int) -> dict:
    with get_catalog_engine().begin() as conn:
        deleted = conn.execute(text("DELETE FROM business_annotation WHERE annotation_id=:id RETURNING annotation_id"),
                               {"id": annotation_id}).scalar()
        if deleted is None:
            raise HTTPException(status_code=404, detail="Annotation not found")
        return {"status": "deleted", "annotation_id": deleted}
