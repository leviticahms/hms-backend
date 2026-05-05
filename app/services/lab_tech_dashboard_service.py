"""
Aggregates data for the Lab Technician dashboard.
Uses live `lab_equipment` rows for status/alerts; bulk test/QC KPIs stay zero until wired to orders.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import Equipment
from app.schemas.lab_tech_dashboard import LabTechDashboardResponse


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _ui_equipment_status(eq: Equipment) -> Tuple[str, str]:
    """
    Map DB row to UI badge: operational | maintenance | calibration_due | inactive
    """
    st = (eq.status or "").upper()
    if st in ("UNDER_MAINTENANCE", "DOWN"):
        return "maintenance", st.replace("_", " ").title()
    if st == "INACTIVE" or not eq.is_active:
        return "inactive", "Inactive"
    nxt = eq.next_calibration_due_at
    if nxt is not None:
        nxt_utc = nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)
        if nxt_utc.date() <= _utc_today() + timedelta(days=7):
            return "calibration_due", "Calibration due"
    if st == "ACTIVE":
        return "operational", "Operational"
    return "operational", st or "Unknown"


class LabTechDashboardService:
    def __init__(self, db: AsyncSession, hospital_id: uuid.UUID):
        self.db = db
        self.hospital_id = hospital_id

    async def _load_equipment(self) -> List[Equipment]:
        res = await self.db.execute(
            select(Equipment)
            .where(Equipment.hospital_id == self.hospital_id)
            .order_by(Equipment.equipment_code)
        )
        return list(res.scalars().all())

    async def get_dashboard(
        self,
        *,
        for_date: Optional[date] = None,
    ) -> LabTechDashboardResponse:
        equipment = await self._load_equipment()
        d = for_date or _utc_today()
        return self._build(equipment, for_date=d)

    def _build(
        self,
        equipment: List[Equipment],
        *,
        for_date: date,
    ) -> LabTechDashboardResponse:
        from app.schemas.lab_tech_dashboard import (
            EquipmentPointModel,
            EquipmentStatusRow,
            KpiCardModel,
            KpiStripModel,
            LabAlertItem,
            LabTechDashboardMeta,
            QcTrendPanelModel,
            StackedTimeSeriesModel,
            TestsByStatusBarModel,
        )

        meta = LabTechDashboardMeta(
            tests_metrics_available=False,
            qc_metrics_available=False,
            demo_data=False,
            generated_at=datetime.now(timezone.utc),
            for_date=for_date,
        )

        kpis = KpiStripModel(
            total_tests=KpiCardModel(
                value=0,
                subtitle="tests processed today (no test pipeline table yet)",
            ),
            pending_tests=KpiCardModel(value=0, subtitle="awaiting processing"),
            completed_tests=KpiCardModel(
                value=0, subtitle="reports generated", completion_rate_percent=None
            ),
            critical_results=KpiCardModel(
                value=0, subtitle="needs immediate review"
            ),
        )
        alerts: List[LabAlertItem] = []
        for eq in equipment:
            ui, detail = _ui_equipment_status(eq)
            nxt = eq.next_calibration_due_at
            if nxt and ui == "calibration_due":
                alerts.append(
                    LabAlertItem(
                        id=f"cal-{eq.id}",
                        severity="warning",
                        code="EQUIPMENT_CALIBRATION",
                        message=f"{eq.name} requires calibration or review ({detail})",
                        related_equipment_id=eq.id,
                    )
                )
        test_volume = StackedTimeSeriesModel(
            title="Test Volume Over Time",
            labels=[],
            series={},
        )
        categories = []
        by_status = TestsByStatusBarModel()
        qc_trend = QcTrendPanelModel()
        # No fabricated efficiency/downtime — chart stays empty until telemetry exists.
        eq_perf: List[EquipmentPointModel] = []
        weekly = []
        pending_rows = []
        crit_rows = []
        qc_today = []

        # Equipment table — always from DB, merged with UI mapping
        eq_status_rows: List[EquipmentStatusRow] = []
        for eq in equipment:
            ui, detail = _ui_equipment_status(eq)
            eq_status_rows.append(
                EquipmentStatusRow(
                    equipment_id=eq.id,
                    equipment_code=eq.equipment_code,
                    equipment_name=eq.name,
                    ui_status=ui,
                    status_detail=detail,
                    db_status=eq.status or "",
                )
            )

        return LabTechDashboardResponse(
            meta=meta,
            kpis=kpis,
            alerts=alerts,
            test_volume_today=test_volume,
            test_categories=categories,
            tests_by_workflow_status=by_status,
            qc_trend=qc_trend,
            equipment_performance=eq_perf,
            weekly_test_trends=weekly,
            weekly_avg_tests_per_day=None,
            weekly_change_percent=None,
            pending_tests_table=pending_rows,
            critical_results_table=crit_rows,
            equipment_status=eq_status_rows,
            qc_status_today=qc_today,
        )
