"""Generate optional business annotation drafts from local Ollama."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from services.catalog_db import get_catalog_engine

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = ("qwen2.5:3b", "qwen2.5:7b")
logger = logging.getLogger(__name__)


class GeneratedAnnotation(BaseModel):
    data_owner: str = Field(description="Likely team or role owning this data asset")
    definition: str = Field(description="Plain-English description of what the asset contains")
    purpose: str = Field(description="Concise business or operational purpose")
    tags: list[str] = Field(default_factory=list, description="Short domain tags")


def _props(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _flags(col: dict[str, Any], fk: str | None = None) -> list[str]:
    props = _props(col.get("dialect_props"))
    flags = []
    if col.get("is_primary_key"):
        flags.append("PRIMARY KEY")
    if fk:
        flags.append(f"FOREIGN KEY -> {fk}")
    if props.get("unique"):
        flags.append("UNIQUE")
    flags.append("NULLABLE" if col.get("is_nullable") else "NOT NULL")
    if props.get("default") is not None:
        flags.append(f"DEFAULT {props['default']}")
    checks = props.get("check_constraints", props.get("checks", [])) or []
    flags.extend(f"CHECK ({check})" for check in checks)
    return flags


def _table_context(conn, target_id: int) -> str:
    table = conn.execute(text("""SELECT t.table_id,t.table_name,ds.name source_name FROM table_meta t
        JOIN schema_meta s ON s.schema_id=t.schema_id
        JOIN data_source ds ON ds.source_id=s.source_id
        WHERE t.table_id=:id AND t.is_active"""), {"id": target_id}).mappings().first()
    if not table:
        raise ValueError("Table not found in catalog")
    columns = conn.execute(text("""SELECT c.column_name,c.is_primary_key FROM column_meta c
        WHERE c.table_id=:id AND c.is_active ORDER BY c.ordinal_position"""), {"id": target_id}).mappings().all()
    names = [f"{col['column_name']} (primary identifier)" if col["is_primary_key"] else col["column_name"]
             for col in columns]
    return (f"Asset: A database table named '{table['table_name']}' in the '{table['source_name']}' system.\n"
            f"Information represented: {', '.join(names) or 'No fields are currently documented'}.")


def _column_context(conn, target_id: int) -> str:
    col = conn.execute(text("""SELECT c.column_id,c.table_id,c.column_name,c.is_primary_key,
        c.is_nullable,t.table_name,ds.name source_name,
        a.definition table_definition,a.data_owner table_owner FROM column_meta c
        JOIN table_meta t ON t.table_id=c.table_id JOIN schema_meta s ON s.schema_id=t.schema_id
        JOIN data_source ds ON ds.source_id=s.source_id LEFT JOIN type_mapping tm ON tm.type_mapping_id=c.type_mapping_id
        LEFT JOIN business_annotation a ON a.table_id=t.table_id
        WHERE c.column_id=:id AND c.is_active AND t.is_active ORDER BY a.annotation_id DESC LIMIT 1"""),
        {"id": target_id}).mappings().first()
    if not col:
        raise ValueError("Column not found in catalog")
    fk = conn.execute(text("""SELECT pt.table_name ref_table,pc.column_name ref_column FROM column_references cr
        JOIN column_meta pc ON pc.column_id=cr.parent_column_id JOIN table_meta pt ON pt.table_id=pc.table_id
        WHERE cr.child_column_id=:id LIMIT 1"""), {"id": target_id}).mappings().first()
    siblings = conn.execute(text("SELECT column_name FROM column_meta WHERE table_id=:table AND is_active AND column_id<>:id ORDER BY ordinal_position"),
                            {"table": col["table_id"], "id": target_id}).scalars().all()
    role = "the table's primary identifier" if col["is_primary_key"] else (
        "an optional attribute" if col["is_nullable"] else "a required attribute")
    relationship = f" It connects to {fk['ref_table']}.{fk['ref_column']}." if fk else ""
    return (f"Asset: The '{col['column_name']}' field in the '{col['table_name']}' table of the '{col['source_name']}' system.\n"
            f"Role: This field serves as {role}.{relationship}\n"
            f"Table description: {col['table_definition'] or 'No table description is available.'}\n"
            f"Table owner: {col['table_owner'] or 'Not assigned.'}\n"
            f"Other information kept with this record: {', '.join(siblings) or 'No other fields.'}")


def _call_model(model: str, context: str) -> dict[str, Any]:
    prompt = (
        "Write a brief, natural-English business glossary note for a non-technical teammate.\n"
        "Rules: Explain in plain English what the asset represents and why the business tracks it. "
        "Never repeat or list field names. Never use SQL type names, SQL expressions, or constraint terms "
        "such as PRIMARY KEY, NOT NULL, DEFAULT, or CHECK in the definition or purpose. "
        "Do not guess details unsupported by the context. For data_owner, choose the most plausible "
        "internal team from Sales Operations, Customer Success, HR & People, Supply Chain, Product Catalog, "
        "or Data Stewardship. Never name an external company or cloud vendor.\n\n"
        "Example input: A table named 'invoices' in the 'Billing' system, containing customer bills, "
        "amounts owed, payment deadlines, and payment status.\n"
        'Example JSON: {"data_owner":"Finance & Billing","definition":"A record of bills issued to customers for completed orders, including the amount owed and when payment is due.",'
        '"purpose":"Helps the finance team follow up on overdue payments and report revenue.","tags":["Billing","Revenue"]}\n\n'
        "Write new wording for this asset; do not copy the example.\n" + context
    )
    payload = {"model": model, "prompt": prompt,
               "format": GeneratedAnnotation.model_json_schema(), "stream": False, "keep_alive": "10m",
               "options": {"temperature": 0.2, "num_predict": 220}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    logger.info("AI annotation: requesting Ollama model %s (%d context characters)", model, len(context))
    with urllib.request.urlopen(request, timeout=90) as response:
        body = json.loads(response.read().decode())
    logger.info("AI annotation: received Ollama response from %s", model)
    try:
        draft = GeneratedAnnotation.model_validate_json(body.get("response", ""))
    except ValidationError:
        logger.exception("AI annotation: Ollama response did not match the expected JSON schema (%s)", model)
        raise
    return {"model": model, "data_owner": draft.data_owner.strip(),
            "definition": draft.definition.strip(), "purpose": draft.purpose.strip(),
            "tags": [tag.strip() for tag in draft.tags if tag.strip()][:5]}


def generate_ai_annotation(target_type: str, target_id: int) -> dict[str, Any]:
    logger.info("AI annotation: building context for %s target_id=%s", target_type, target_id)
    try:
        with get_catalog_engine().connect() as conn:
            if target_type == "TABLE":
                context = _table_context(conn, target_id)
            elif target_type == "COLUMN":
                context = _column_context(conn, target_id)
            else:
                raise ValueError("target_type must be TABLE or COLUMN")
    except Exception:
        logger.exception("AI annotation: failed to build catalog context for %s target_id=%s", target_type, target_id)
        raise
    logger.info("AI annotation: catalog context ready (%d characters)", len(context))
    last_error: Exception | None = None
    for model in MODELS:
        try:
            return _call_model(model, context)
        except urllib.error.HTTPError as exc:
            last_error = exc
            logger.warning("AI annotation: Ollama returned HTTP %s for model %s", exc.code, model)
            if exc.code == 404:
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            logger.exception("AI annotation: request or response failed for model %s", model)
            break
    logger.error("AI annotation: all model attempts failed; last error type=%s", type(last_error).__name__ if last_error else "unknown")
    raise RuntimeError(f"Ollama annotation generation failed ({type(last_error).__name__ if last_error else 'unknown error'})") from last_error
