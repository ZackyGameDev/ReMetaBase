"""Fragment group and relationship catalog endpoints."""

from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from services.catalog_db import get_catalog_engine

router = APIRouter(prefix="/api/lineage", tags=["Fragmentations & Cross-DB Lineage"])


class FragmentCreateRequest(BaseModel):
    logical_name: str = Field(min_length=1, max_length=120)
    fragment_type: Literal["HORIZONTAL", "VERTICAL"]
    table_id: int = Field(gt=0)
    predicate: str = Field(default="", max_length=255)


class CrossDbLinkCreateRequest(BaseModel):
    child_column_id: int = Field(gt=0)
    parent_column_id: int = Field(gt=0)


@router.get("/overview")
def get_lineage_overview() -> dict[str, Any]:
    with get_catalog_engine().connect() as conn:
        tables = [dict(row) for row in conn.execute(text("""SELECT t.table_id,t.table_name,s.schema_name,ds.source_id,
            ds.name source_name,ds.db_type FROM table_meta t JOIN schema_meta s ON s.schema_id=t.schema_id
            JOIN data_source ds ON ds.source_id=s.source_id WHERE t.is_active ORDER BY ds.name,t.table_name""")).mappings()]
        columns = [dict(row) for row in conn.execute(text("""SELECT c.column_id,c.column_name,c.raw_type,c.is_primary_key,
            t.table_id,t.table_name,s.schema_name,ds.source_id,ds.name source_name,ds.db_type
            FROM column_meta c JOIN table_meta t ON t.table_id=c.table_id JOIN schema_meta s ON s.schema_id=t.schema_id
            JOIN data_source ds ON ds.source_id=s.source_id WHERE c.is_active AND t.is_active
            ORDER BY ds.name,t.table_name,c.ordinal_position""")).mappings()]
        fragments = conn.execute(text("""SELECT f.fragment_id,f.logical_name,f.fragment_type,f.predicate,t.table_id,
            t.table_name,s.schema_name,ds.name source_name,ds.db_type FROM table_fragment f
            JOIN table_meta t ON t.table_id=f.table_id JOIN schema_meta s ON s.schema_id=t.schema_id
            JOIN data_source ds ON ds.source_id=s.source_id WHERE t.is_active ORDER BY f.logical_name,ds.name,t.table_name""")).mappings().all()
        groups: dict[tuple[str,str],dict[str,Any]] = {}
        registered = set()
        for row in fragments:
            group_key = (row["logical_name"], row["fragment_type"])
            group = groups.setdefault(group_key,{"logical_name":row["logical_name"],"fragment_type":row["fragment_type"],"members":[]})
            group["members"].append(dict(row))
            registered.add(row["table_id"])

        refs = conn.execute(text("""SELECT cr.ref_id reference_id,cr.is_logical_cross_db,
            cc.column_id child_column_id,cc.column_name child_column_name,ct.table_id child_table_id,ct.table_name child_table_name,
            cds.name child_source_name,cds.db_type child_db_type,pc.column_id parent_column_id,pc.column_name parent_column_name,
            pt.table_id parent_table_id,pt.table_name parent_table_name,pds.name parent_source_name,pds.db_type parent_db_type
            FROM column_references cr JOIN column_meta cc ON cc.column_id=cr.child_column_id
            JOIN table_meta ct ON ct.table_id=cc.table_id JOIN schema_meta cs ON cs.schema_id=ct.schema_id
            JOIN data_source cds ON cds.source_id=cs.source_id JOIN column_meta pc ON pc.column_id=cr.parent_column_id
            JOIN table_meta pt ON pt.table_id=pc.table_id JOIN schema_meta ps ON ps.schema_id=pt.schema_id
            JOIN data_source pds ON pds.source_id=ps.source_id WHERE cc.is_active AND pc.is_active AND ct.is_active AND pt.is_active""")).mappings().all()
        links = [dict(row) for row in refs]
        existing_pairs = {(r["child_column_id"],r["parent_column_id"]) for r in links}
        col_by_table: dict[int,list[str]] = defaultdict(list)
        for col in columns:
            col_by_table[col["table_id"]].append(col["column_name"].lower())
        signatures: dict[tuple[str,...],list[dict[str,Any]]] = defaultdict(list)
        for table in tables:
            signature=tuple(sorted(col_by_table[table["table_id"]]))
            if len(signature)>1:
                signatures[signature].append(table)
        horizontal=[]
        for matching in signatures.values():
            unregistered=[t for t in matching if t["table_id"] not in registered]
            if len(unregistered)>1:
                horizontal.append({"suggested_logical_name":unregistered[0]["table_name"].split("_")[0].capitalize(),
                                   "fragment_type":"HORIZONTAL","tables":unregistered})
        by_name: dict[str,list[dict[str,Any]]] = defaultdict(list)
        for col in columns:
            by_name[col["column_name"].lower()].append(col)
        cross=[]
        for matched in by_name.values():
            for parent in ([c for c in matched if c["is_primary_key"]] or matched[:1]):
                for child in matched:
                    pair=(child["column_id"],parent["column_id"])
                    if child["source_id"]!=parent["source_id"] and child["column_id"]!=parent["column_id"] and pair not in existing_pairs:
                        cross.append({"child_column_id":child["column_id"],"child_label":f"{child['source_name']}.{child['table_name']}.{child['column_name']}",
                                      "parent_column_id":parent["column_id"],"parent_label":f"{parent['source_name']}.{parent['table_name']}.{parent['column_name']}"})
        return {"fragment_groups":list(groups.values()),"links":links,"horizontal_suggestions":horizontal,
                "cross_db_suggestions":cross,"tables":tables,"columns":columns}


@router.post("/fragments")
def register_fragment(payload: FragmentCreateRequest) -> dict:
    with get_catalog_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM table_meta WHERE table_id=:id AND is_active"),{"id":payload.table_id}).scalar():
            raise HTTPException(404,"Active table not found")
        row=conn.execute(text("""INSERT INTO table_fragment(logical_name,fragment_type,table_id,predicate)
            VALUES (:name,:type,:table,:predicate) ON CONFLICT(logical_name,fragment_type,table_id) DO NOTHING
            RETURNING fragment_id"""),{"name":payload.logical_name.strip(),"type":payload.fragment_type,
            "table":payload.table_id,"predicate":payload.predicate.strip() or None}).scalar()
        if row is None:
            raise HTTPException(409,"This table is already in that fragment group")
        return {"status":"created","fragment_id":row}


@router.delete("/fragments/{fragment_id}")
def delete_fragment(fragment_id: int) -> dict:
    with get_catalog_engine().begin() as conn:
        deleted=conn.execute(text("DELETE FROM table_fragment WHERE fragment_id=:id RETURNING fragment_id"),{"id":fragment_id}).scalar()
        if deleted is None:
            raise HTTPException(404,"Fragment entry not found")
        return {"status":"deleted","fragment_id":deleted}


@router.post("/links")
def create_cross_db_link(payload: CrossDbLinkCreateRequest) -> dict:
    with get_catalog_engine().begin() as conn:
        pair=conn.execute(text("""SELECT c.column_id child,p.column_id parent,cs.source_id child_source,ps.source_id parent_source
            FROM column_meta c JOIN table_meta ct ON ct.table_id=c.table_id JOIN schema_meta cs ON cs.schema_id=ct.schema_id
            JOIN column_meta p ON p.column_id=:parent JOIN table_meta pt ON pt.table_id=p.table_id JOIN schema_meta ps ON ps.schema_id=pt.schema_id
            WHERE c.column_id=:child AND c.is_active AND p.is_active AND ct.is_active AND pt.is_active"""),
            {"child":payload.child_column_id,"parent":payload.parent_column_id}).mappings().first()
        if not pair:
            raise HTTPException(404,"Active child or parent column not found")
        if pair["child"]==pair["parent"]:
            raise HTTPException(400,"A column cannot link to itself")
        if pair["child_source"]==pair["parent_source"]:
            raise HTTPException(400,"Logical links are only for columns in different data sources")
        try:
            ref=conn.execute(text("""INSERT INTO column_references(child_column_id,parent_column_id,is_logical_cross_db)
                VALUES (:child,:parent,TRUE) RETURNING ref_id"""),{"child":pair["child"],"parent":pair["parent"]}).scalar()
        except Exception as exc:
            raise HTTPException(409,"This relationship already exists") from exc
        return {"status":"created","reference_id":ref}


@router.delete("/links/{reference_id}")
def delete_cross_db_link(reference_id: int) -> dict:
    with get_catalog_engine().begin() as conn:
        deleted=conn.execute(text("DELETE FROM column_references WHERE ref_id=:id AND is_logical_cross_db=TRUE RETURNING ref_id"),
                              {"id":reference_id}).scalar()
        if deleted is None:
            raise HTTPException(404,"Logical link not found; physical foreign keys cannot be removed manually")
        return {"status":"deleted","reference_id":deleted}
