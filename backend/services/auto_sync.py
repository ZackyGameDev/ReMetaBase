"""Configurable, non-overlapping background metadata sync manager."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from schemas import DatabaseTargetConfig


class AutoSyncConfigRequest(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=3, le=300)
    targets: list[DatabaseTargetConfig] | None = None
    sample_limit: int | None = Field(default=None, ge=1, le=50)


class AutoSyncManager:
    def __init__(self, default_interval: int = 7) -> None:
        self.enabled = True
        self.interval_seconds = default_interval
        self.sample_limit = 5
        self.targets: list[DatabaseTargetConfig] = []
        self.last_checked_at: str | None = None
        self.event_id = 0
        self.last_reports: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    def configure(self, request: AutoSyncConfigRequest) -> dict[str, Any]:
        if request.enabled is not None:
            self.enabled = request.enabled
        if request.interval_seconds is not None:
            self.interval_seconds = request.interval_seconds
        if request.targets is not None:
            self.targets = request.targets
        if request.sample_limit is not None:
            self.sample_limit = request.sample_limit
        return self.status()

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "interval_seconds": self.interval_seconds,
                "configured_targets": sum(target.selected for target in self.targets),
                "last_checked_at": self.last_checked_at, "event_id": self.event_id,
                "last_reports": self.last_reports}

    def _run_cycle(self) -> list[dict[str, Any]]:
        from services.catalog_repository import save_sync_to_catalog
        from services.metadata_extractor import extract_database_metadata

        reports = []
        for target in list(self.targets):
            if not target.selected:
                continue
            extracted = extract_database_metadata(target, self.sample_limit)
            if extracted.status != "success":
                reports.append({"id": target.id, "label": target.label, "status": "error", "error": extracted.error})
                continue
            try:
                report = save_sync_to_catalog(target, extracted)
                reports.append({"id": target.id, "label": target.label, "status": "success", **report})
            except Exception as exc:
                reports.append({"id": target.id, "label": target.label, "status": "error", "error": str(exc)})
        return reports

    async def tick(self) -> None:
        if not self.enabled or not any(target.selected for target in self.targets) or self._lock.locked():
            return
        async with self._lock:
            reports = await asyncio.to_thread(self._run_cycle)
            self.last_checked_at = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            changed = any(report.get("status") == "success" and report.get("changed") for report in reports)
            if changed or any(report.get("status") == "error" for report in reports):
                self.event_id += 1
                self.last_reports = reports

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                try:
                    await self.tick()
                except Exception as exc:
                    self.last_checked_at = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                    self.event_id += 1
                    self.last_reports = [{"status": "error", "error": str(exc)}]

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
            self._task = None


auto_sync_manager = AutoSyncManager()
