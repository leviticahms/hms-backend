"""
Service for Secure Result Access UI.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lab_portal import LabResultAccessGrant, LabResultAccessLog
from app.schemas.lab_result_access import (
    GrantResultAccessRequest,
    GrantResultAccessResponse,
    ResultAccessDashboardResponse,
    ResultAccessLogRow,
    ResultAccessMeta,
    ResultAccessPatientRow,
    ResultAccessStatCards,
)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_access_log_date(access_time: str) -> date | None:
    s = (access_time or "").strip()
    if len(s) >= 10:
        for chunk, fmt in (
            (s[:10], "%Y-%m-%d"),
            (s[:10], "%d-%m-%Y"),
            (s[:10], "%d/%m/%Y"),
        ):
            try:
                return datetime.strptime(chunk, fmt).date()
            except ValueError:
                continue
    return None


def _is_mobile_client_agent(device_browser: str) -> bool:
    u = (device_browser or "").lower()
    return any(k in u for k in ("mobile", "android", "iphone", "ipad", "okhttp"))


def _phone_dashboard(v: str | None) -> str:
    s = (v or "").strip()
    return s if s else "N/A"


def _last_access_dashboard(v: str | None) -> str:
    s = (v or "").strip()
    return s if s else "Never"


def _access_log_report_type(action: str) -> str:
    """Label for UI ``REPORT TYPE`` column (no dedicated DB column)."""
    a = (action or "").strip().upper()
    if not a:
        return "Lab results"
    if "PDF" in a or "DOWNLOAD" in a:
        return "Full report (PDF)"
    if "SHARE" in a or "EMAIL" in a or "SENT" in a:
        return "Shared report"
    if "VIEW" in a or "OPEN" in a or "PORTAL" in a:
        return "Online view"
    return (action or "").strip()


class LabResultAccessService:
    def __init__(self, db: AsyncSession, hospital_id):
        self.db = db
        self.hospital_id = hospital_id

    def _stats_from_rows(self, p_recs: list, l_recs: list) -> ResultAccessStatCards:
        today = _utc_today()
        active_access = sum(1 for r in p_recs if (r.status or "").upper() == "ACTIVE")
        doctor_access = sum(1 for r in p_recs if r.doctor_name and str(r.doctor_name).strip())
        todays_accesses = sum(
            1 for r in l_recs if (d := _parse_access_log_date(r.access_time)) is not None and d == today
        )
        mobile_accesses = sum(1 for r in l_recs if _is_mobile_client_agent(r.device_browser))
        return ResultAccessStatCards(
            active_access=active_access,
            doctor_access=doctor_access,
            todays_accesses=todays_accesses,
            mobile_accesses=mobile_accesses,
        )

    async def get_dashboard(self, *, search: str | None = None, status: str | None = None) -> ResultAccessDashboardResponse:
        p_stmt = select(LabResultAccessGrant).where(LabResultAccessGrant.hospital_id == self.hospital_id)
        l_stmt = select(LabResultAccessLog).where(LabResultAccessLog.hospital_id == self.hospital_id)
        p_recs = list((await self.db.execute(p_stmt)).scalars().all())
        l_recs = list((await self.db.execute(l_stmt)).scalars().all())
        stats = self._stats_from_rows(p_recs, l_recs)

        patients = [
            ResultAccessPatientRow(
                patient_ref=r.patient_ref,
                patient_name=r.patient_name,
                email=r.email,
                phone=_phone_dashboard(r.phone),
                last_access=_last_access_dashboard(r.last_access),
                access_count=r.access_count,
                status=str(r.status or "").strip().upper() or "ACTIVE",
                access_code=r.access_code,
                access_type=str(r.access_type or "VIEW_ONLY").strip().upper() or "VIEW_ONLY",
            )
            for r in p_recs
        ]
        logs = [
            ResultAccessLogRow(
                patient_ref=r.patient_ref,
                patient_name=r.patient_name,
                accessed_by=r.accessed_by,
                access_time=r.access_time,
                action=r.action,
                report_type=_access_log_report_type(r.action),
                ip_address=r.ip_address,
                device_browser=r.device_browser,
            )
            for r in l_recs
        ]

        if search:
            q = search.strip().lower()
            patients = [
                p
                for p in patients
                if q in p.patient_name.lower() or q in p.patient_ref.lower() or q in p.email.lower()
            ]
            logs = [
                l
                for l in logs
                if q in l.patient_name.lower()
                or q in l.accessed_by.lower()
                or (l.patient_ref and q in l.patient_ref.lower())
            ]
        if status:
            s = status.strip().upper()
            patients = [p for p in patients if str(p.status).strip().upper() == s]

        return ResultAccessDashboardResponse(
            meta=ResultAccessMeta(
                generated_at=datetime.now(timezone.utc),
                live_data=True,
                demo_data=False,
            ),
            stats=stats,
            patients=patients,
            access_logs=logs,
            security_features=[
                "Encrypted Links",
                "Access Control",
                "Audit Trail",
            ],
        )

    async def grant_access(self, payload: GrantResultAccessRequest) -> GrantResultAccessResponse:
        display_name = (payload.patient_name or payload.patient_ref).strip()
        code = f"ACC-{uuid.uuid4().hex[:10].upper()}"
        rec = LabResultAccessGrant(
            hospital_id=self.hospital_id,
            patient_ref=payload.patient_ref,
            patient_name=display_name,
            doctor_name=None,
            email=payload.email,
            phone=(payload.phone or "").strip(),
            access_type=payload.access_type,
            status="ACTIVE",
            access_count=0,
            access_code=code,
            expiry_date=payload.expiry_date,
            last_access=None,
        )
        self.db.add(rec)
        await self.db.commit()
        return GrantResultAccessResponse(
            message="Secure result access granted successfully.",
            patient_ref=payload.patient_ref,
            email=payload.email,
            access_type=payload.access_type,
            expiry_date=payload.expiry_date,
            access_code=code,
        )
