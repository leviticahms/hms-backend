"""
Service for Equipment Tracking screen.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.schemas.lab_equipment_tracking import (
    AddEquipmentTrackingRequest,
    AddEquipmentTrackingResponse,
    EquipmentTrackingActionResponse,
    EquipmentTrackingDashboardResponse,
    EquipmentTrackingMeta,
    EquipmentTrackingRow,
    EquipmentTrackingStatCards,
    MaintenanceLogTrackingRow,
)
from app.services.lab_service import LabService


class LabEquipmentTrackingService:
    def __init__(self, lab_service: LabService):
        self.lab = lab_service

    @staticmethod
    def _is_swagger_placeholder_equipment(e: dict) -> bool:
        """Hide rows created from Swagger/UI defaults where every field was left as 'string'."""
        name = (e.get("equipment_name") or "").strip().lower()
        cat = (e.get("category") or "").strip().lower()
        mfg = (e.get("manufacturer") or "").strip().lower()
        model = (e.get("model") or "").strip().lower()
        serial = (e.get("serial_number") or "").strip().lower()
        loc = (e.get("location") or "").strip().lower()
        if name == "string":
            return True
        return (
            cat == "string"
            and mfg == "string"
            and model == "string"
            and serial == "string"
            and loc == "string"
        )

    async def dashboard(self, *, search: Optional[str] = None) -> EquipmentTrackingDashboardResponse:
        eq_data = await self.lab.get_equipment_list(page=1, limit=200, active_only=False)
        logs_data = await self.lab.get_maintenance_logs(page=1, limit=20)
        raw_equipment = [
            e for e in eq_data.get("equipment", []) if not self._is_swagger_placeholder_equipment(e)
        ]
        rows = [
            EquipmentTrackingRow(
                equipment_id=e["equipment_id"],
                equipment_code=e["equipment_code"],
                name=e.get("equipment_name", ""),
                equipment_type=e.get("category", "GENERAL"),
                brand=e.get("manufacturer"),
                model=e.get("model"),
                serial_no=e.get("serial_number"),
                location=e.get("location"),
                status=self._map_status(e.get("status"), e.get("next_calibration_due_at")),
            )
            for e in raw_equipment
        ]
        logs = [
            MaintenanceLogTrackingRow(
                equipment=l.get("equipment_name", ""),
                maintenance_type=l.get("type", ""),
                date=str(l.get("performed_at", ""))[:10],
                performed_by=str(l.get("performed_by", "")),
                cost=float(l["cost"]) if l.get("cost") is not None else None,
                description=l.get("remarks") or "",
            )
            for l in logs_data.get("logs", [])
        ]

        if search:
            q = search.strip().lower()
            rows = [r for r in rows if q in r.name.lower() or q in r.equipment_code.lower()]

        stats = EquipmentTrackingStatCards(
            total_equipment=len(rows),
            operational=sum(1 for r in rows if r.status == "OPERATIONAL"),
            maintenance=sum(1 for r in rows if r.status == "MAINTENANCE"),
            calibration_due=sum(1 for r in rows if r.status == "CALIBRATION_DUE"),
        )
        return EquipmentTrackingDashboardResponse(
            meta=EquipmentTrackingMeta(generated_at=datetime.now(timezone.utc)),
            stats=stats,
            equipment_list=rows,
            maintenance_logs=logs,
            quick_actions=["BULK_QR_CODES", "MAINTENANCE_SCHEDULE", "CALIBRATION_REPORT", "EXPORT_INVENTORY"],
        )

    async def add_equipment(self, payload: AddEquipmentTrackingRequest) -> AddEquipmentTrackingResponse:
        code = f"EQP-{uuid.uuid4().hex[:10].upper()}"
        created = await self.lab.create_equipment(
            {
                "equipment_code": code,
                "equipment_name": payload.equipment_name,
                "category": payload.equipment_type.upper().replace(" ", "_")[:20],
                "manufacturer": payload.brand,
                "model": payload.model,
                "serial_number": payload.serial_number,
                "location": payload.location,
                "next_calibration_due_at": payload.next_maintenance_date,
            }
        )
        if payload.initial_status != "OPERATIONAL":
            mapped = "UNDER_MAINTENANCE" if payload.initial_status == "MAINTENANCE" else "INACTIVE"
            await self.lab.update_equipment_status(created["equipment_id"], mapped)
        return AddEquipmentTrackingResponse(
            message="Equipment added successfully.",
            equipment_id=created["equipment_id"],
            equipment_code=created["equipment_code"],
            status=payload.initial_status if payload.initial_status != "MAINTENANCE" else "MAINTENANCE",
        )

    def quick_action(self, action: str) -> EquipmentTrackingActionResponse:
        return EquipmentTrackingActionResponse(message=f"{action} initiated.", action=action)

    def _map_status(self, status: Optional[str], next_due: Optional[datetime]) -> str:
        s = (status or "").upper()
        if s in ("UNDER_MAINTENANCE", "DOWN"):
            return "MAINTENANCE"
        if s == "INACTIVE":
            return "INACTIVE"
        if next_due:
            due = next_due if next_due.tzinfo else next_due.replace(tzinfo=timezone.utc)
            if due.date() <= (datetime.now(timezone.utc) + timedelta(days=7)).date():
                return "CALIBRATION_DUE"
        return "OPERATIONAL"
