"""
Service layer for Quality Control workflows UI.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabQcMaterial, LabQcRule, LabQcRun
from app.schemas.lab_quality_control import (
    QcMaterialRow,
    QcRuleRow,
    QcRunRow,
    QcStatCards,
    QcWorkflowActionResponse,
    QualityControlDashboardResponse,
    QualityControlMeta,
    RecordQcRunRequest,
    RecordQcRunResponse,
)


def _qc_run_date_is_today(run_date: str, *, today: date) -> bool:
    s = (run_date or "").strip()
    if len(s) < 8:
        return False
    chunk = s[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(chunk, fmt).date() == today
        except ValueError:
            continue
    return False


class LabQualityControlService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    async def dashboard(self) -> QualityControlDashboardResponse:
        runs, mats, rules = await self._db_rows()
        today = datetime.now(timezone.utc).date()
        stats = QcStatCards(
            todays_qc_runs=sum(1 for r in runs if _qc_run_date_is_today(r.date, today=today)),
            passed_runs=sum(1 for r in runs if r.status == "PASSED"),
            warning_runs=sum(1 for r in runs if r.status == "WARNING"),
            failed_runs=sum(1 for r in runs if r.status == "FAILED"),
        )
        return QualityControlDashboardResponse(
            meta=QualityControlMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=True,
                demo_data=False,
            ),
            stats=stats,
            qc_runs=runs,
            materials_inventory=mats,
            rules=rules,
            workflow_actions=["LEVEY_JENNINGS_CHART", "QC_COMPLIANCE_REPORT", "QC_ALERTS"],
        )

    async def record_qc_run(self, payload: RecordQcRunRequest) -> RecordQcRunResponse:
        if payload.observed_value <= 0:
            status = "FAILED"
        elif payload.observed_value > 100:
            status = "WARNING"
        else:
            status = "PASSED"
        qc_id = f"QC-{uuid.uuid4().hex[:12].upper()}"
        rec = LabQcRun(
            hospital_id=self.hospital_id,
            qc_id=qc_id,
            test=payload.test,
            qc_material=payload.qc_material,
            lot_number=payload.lot_number,
            run_date=payload.date,
            operator=payload.operator,
            status=status,
            observed_value=payload.observed_value,
        )
        self.db.add(rec)
        await self.db.commit()
        return RecordQcRunResponse(
            message="QC run recorded successfully.",
            qc_id=qc_id,
            status=status,  # type: ignore[arg-type]
        )

    async def workflow_action(self, action: str) -> QcWorkflowActionResponse:
        return QcWorkflowActionResponse(
            message=f"{action} initiated successfully.",
            action=action,
        )

    async def _db_rows(self):
        runs = (await self.db.execute(select(LabQcRun).where(LabQcRun.hospital_id == self.hospital_id))).scalars().all()
        mats = (await self.db.execute(select(LabQcMaterial).where(LabQcMaterial.hospital_id == self.hospital_id))).scalars().all()
        rules = (await self.db.execute(select(LabQcRule).where(LabQcRule.hospital_id == self.hospital_id))).scalars().all()
        return (
            [QcRunRow(qc_id=r.qc_id, test=r.test, qc_material=r.qc_material, lot_number=r.lot_number, date=r.run_date, operator=r.operator, status=r.status, observed_value=float(r.observed_value)) for r in runs],
            [QcMaterialRow(material_name=m.material_name, material_type=m.material_type, manufacturer=m.manufacturer, lot_number=m.lot_number, expiry_date=m.expiry_date, storage=m.storage, quantity=m.quantity) for m in mats],
            [QcRuleRow(rule_name=x.rule_name, description=x.description, rule_type=x.rule_type, action_required=x.action_required, priority=x.priority) for x in rules],
        )

