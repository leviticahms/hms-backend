"""
Aggregates data for the Lab Technician dashboard.
Uses live lab portal tables for KPIs/charts and live `lab_equipment` rows for status/alerts.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab import Equipment
from app.models.lab_portal import (
    LabCriticalAlert,
    LabQcRun,
    LabReportRecord,
    LabTestRegistration,
)
from app.models.prescription import PrescriptionLabOrder
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


def _row_date(row, date_attr: str) -> Optional[date]:
    value = getattr(row, date_attr, None)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    created_at = getattr(row, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.date()
    return None


def _row_hour_label(row) -> str:
    created_at = getattr(row, "created_at", None)
    if isinstance(created_at, datetime):
        return created_at.strftime("%H:00")
    return "Unknown"


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

    async def _load_test_registrations(self) -> List[LabTestRegistration]:
        res = await self.db.execute(
            select(LabTestRegistration)
            .where(LabTestRegistration.hospital_id == self.hospital_id)
            .order_by(LabTestRegistration.created_at.desc())
        )
        return list(res.scalars().all())

    async def _load_reports(self) -> List[LabReportRecord]:
        res = await self.db.execute(
            select(LabReportRecord)
            .where(LabReportRecord.hospital_id == self.hospital_id)
            .order_by(LabReportRecord.created_at.desc())
        )
        return list(res.scalars().all())

    async def _load_critical_alerts(self) -> List[LabCriticalAlert]:
        res = await self.db.execute(
            select(LabCriticalAlert)
            .where(LabCriticalAlert.hospital_id == self.hospital_id)
            .order_by(LabCriticalAlert.created_at.desc())
        )
        return list(res.scalars().all())

    async def _load_qc_runs(self) -> List[LabQcRun]:
        res = await self.db.execute(
            select(LabQcRun)
            .where(LabQcRun.hospital_id == self.hospital_id)
            .order_by(LabQcRun.created_at.desc())
        )
        return list(res.scalars().all())

    async def _load_prescription_lab_orders(self) -> List[PrescriptionLabOrder]:
        res = await self.db.execute(
            select(PrescriptionLabOrder)
            .where(PrescriptionLabOrder.hospital_id == self.hospital_id)
            .order_by(PrescriptionLabOrder.created_at.desc())
        )
        return list(res.scalars().all())

    async def get_dashboard(
        self,
        *,
        for_date: Optional[date] = None,
    ) -> LabTechDashboardResponse:
        equipment = await self._load_equipment()
        registrations = await self._load_test_registrations()
        reports = await self._load_reports()
        critical_alerts = await self._load_critical_alerts()
        qc_runs = await self._load_qc_runs()
        prescription_orders = await self._load_prescription_lab_orders()
        d = for_date or _utc_today()
        return self._build(
            equipment,
            registrations,
            reports,
            critical_alerts,
            qc_runs,
            prescription_orders,
            for_date=d,
        )

    def _build(
        self,
        equipment: List[Equipment],
        registrations: List[LabTestRegistration],
        reports: List[LabReportRecord],
        critical_alerts: List[LabCriticalAlert],
        qc_runs: List[LabQcRun],
        prescription_orders: List[PrescriptionLabOrder],
        *,
        for_date: date,
    ) -> LabTechDashboardResponse:
        from app.schemas.lab_tech_dashboard import (
            CategorySliceModel,
            DashboardTableRowCritical,
            DashboardTableRowPending,
            EquipmentPointModel,
            EquipmentStatusRow,
            KpiCardModel,
            KpiStripModel,
            LabAlertItem,
            LabTechDashboardMeta,
            QcStatusTodayRow,
            QcTrendPanelModel,
            QcTrendPointModel,
            StackedTimeSeriesModel,
            TestsByStatusBarModel,
            WeeklyDayPointModel,
        )

        completed_registrations = [r for r in registrations if (r.status or "").upper() == "COMPLETED"]
        completed_tests = max(len(completed_registrations), len(reports))
        pending_statuses = {"SAMPLE_PENDING", "SAMPLE_COLLECTED", "IN_PROGRESS", "PENDING", "PENDING_REVIEW"}
        pending_registrations = [
            r for r in registrations
            if (r.status or "").upper() in pending_statuses
        ]
        pending_critical = [
            a for a in critical_alerts
            if (a.notify_status or "").upper() == "PENDING"
            or str(a.acknowledged or "").lower() != "true"
        ]
        total_tests = len(registrations) + len(prescription_orders)
        completion_rate = round((completed_tests / total_tests) * 100, 1) if total_tests else 0.0

        meta = LabTechDashboardMeta(
            tests_metrics_available=bool(registrations or reports or critical_alerts or prescription_orders),
            qc_metrics_available=bool(qc_runs),
            demo_data=False,
            generated_at=datetime.now(timezone.utc),
            for_date=for_date,
        )

        kpis = KpiStripModel(
            total_tests=KpiCardModel(
                value=total_tests,
                subtitle="registered tests",
            ),
            pending_tests=KpiCardModel(
                value=len(pending_registrations) + sum(1 for order in prescription_orders if not order.sent_to_lab),
                subtitle="awaiting processing",
            ),
            completed_tests=KpiCardModel(
                value=completed_tests,
                subtitle="completed / reports generated",
                completion_rate_percent=completion_rate,
            ),
            critical_results=KpiCardModel(
                value=len(pending_critical), subtitle="needs immediate review"
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

        for alert in pending_critical[:5]:
            alerts.append(
                LabAlertItem(
                    id=f"crit-{alert.alert_id}",
                    severity="critical",
                    code="CRITICAL_PENDING",
                    message=f"{alert.patient_name} - {alert.test_name}: {alert.result_value}",
                )
            )

        today_registrations = [r for r in registrations if _row_date(r, "registered_date") == for_date]
        today_orders = [o for o in prescription_orders if _row_date(o, "created_at") == for_date]
        today_reports = [r for r in reports if _row_date(r, "completion_date") == for_date]
        received_by_hour = Counter(_row_hour_label(r) for r in today_registrations)
        received_by_hour.update(_row_hour_label(o) for o in today_orders)
        completed_by_hour = Counter(_row_hour_label(r) for r in today_reports)
        labels = sorted(set(received_by_hour) | set(completed_by_hour))
        test_volume = StackedTimeSeriesModel(
            title="Test Volume Over Time",
            labels=labels,
            series={
                "tests_received": [float(received_by_hour[label]) for label in labels],
                "tests_completed": [float(completed_by_hour[label]) for label in labels],
            } if labels else {},
        )

        category_counts = Counter(r.test_type or "Uncategorized" for r in registrations)
        category_counts.update(o.test_category or o.test_name or "Prescription Orders" for o in prescription_orders)
        categories = [
            CategorySliceModel(
                name=name,
                count=count,
                percent=round((count / total_tests) * 100, 1) if total_tests else 0,
            )
            for name, count in category_counts.most_common()
        ]

        status_counts = Counter((r.status or "UNKNOWN").replace("_", " ").title() for r in registrations)
        status_counts.update("Sent To Lab" if o.sent_to_lab else "Ordered" for o in prescription_orders)
        by_status = TestsByStatusBarModel(
            labels=list(status_counts.keys()),
            values=list(status_counts.values()),
        )

        qc_status_counts = Counter((q.status or "UNKNOWN").upper() for q in qc_runs)
        latest_qc = qc_runs[:10]
        qc_trend = QcTrendPanelModel(
            test_name=latest_qc[0].test if latest_qc else "—",
            points=[
                QcTrendPointModel(
                    t=str(q.run_date),
                    value=float(q.observed_value or 0),
                    in_range=(q.status or "").upper() == "PASSED",
                )
                for q in reversed(latest_qc)
            ],
            within_range=qc_status_counts.get("PASSED", 0),
            warnings=qc_status_counts.get("WARNING", 0),
            failures=qc_status_counts.get("FAILED", 0),
        )

        # No fabricated efficiency/downtime — chart stays empty until telemetry exists.
        eq_perf: List[EquipmentPointModel] = []
        weekly = []
        for offset in range(6, -1, -1):
            day = for_date - timedelta(days=offset)
            weekly.append(
                WeeklyDayPointModel(
                    day_label=day.strftime("%a"),
                    total_tests=(
                        sum(1 for r in registrations if _row_date(r, "registered_date") == day)
                        + sum(1 for o in prescription_orders if _row_date(o, "created_at") == day)
                    ),
                    critical_results=sum(1 for a in critical_alerts if _row_date(a, "created_at") == day),
                )
            )
        weekly_avg = round(sum(point.total_tests for point in weekly) / 7, 1) if weekly else None

        pending_rows = [
            DashboardTableRowPending(
                test_id=r.test_id,
                patient_name=r.patient_name,
                test_name=r.test_type,
                status_or_priority=(r.priority or r.status or ""),
            )
            for r in pending_registrations[:10]
        ]
        pending_rows.extend(
            DashboardTableRowPending(
                test_id=str(order.lab_order_id or order.id),
                patient_name="Prescription patient",
                test_name=order.test_name,
                status_or_priority=order.urgency or ("SENT" if order.sent_to_lab else "ORDERED"),
            )
            for order in prescription_orders[: max(0, 10 - len(pending_rows))]
        )
        crit_rows = [
            DashboardTableRowCritical(
                test_id=a.test_id,
                patient_name=a.patient_name,
                test_name=a.test_name,
                value=a.result_value,
            )
            for a in pending_critical[:10]
        ]
        qc_today = [
            QcStatusTodayRow(
                test_name=q.test,
                status=(q.status or "").title(),
                value=str(q.observed_value),
                target=q.qc_material,
            )
            for q in qc_runs
            if str(q.run_date)[:10] == for_date.isoformat()
        ][:10]

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
            weekly_avg_tests_per_day=weekly_avg,
            weekly_change_percent=None,
            pending_tests_table=pending_rows,
            critical_results_table=crit_rows,
            equipment_status=eq_status_rows,
            qc_status_today=qc_today,
        )
