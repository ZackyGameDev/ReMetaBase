"""Request and response models for the metadata inspection API."""

from typing import Any

from pydantic import BaseModel, Field


class DatabaseTargetConfig(BaseModel):
    id: str
    label: str
    dialect: str
    host: str | None = "localhost"
    port: int | None = None
    database: str
    username: str | None = None
    password: str | None = None
    selected: bool = True


class SyncRequest(BaseModel):
    targets: list[DatabaseTargetConfig]
    sample_limit: int = Field(default=5, ge=1, le=50)


class ColumnMetadata(BaseModel):
    name: str
    raw_type: str
    nullable: bool
    default: str | None = None
    is_primary_key: bool = False
    foreign_key: str | None = None
    unique: bool = False
    check_constraints: list[str] = Field(default_factory=list)


class TableMetadata(BaseModel):
    table_name: str
    row_count: int
    columns: list[ColumnMetadata]
    sample_rows: list[dict[str, Any]]


class DatabaseSyncResult(BaseModel):
    id: str
    label: str
    dialect: str
    status: str
    error: str | None = None
    tables: list[TableMetadata] = Field(default_factory=list)
