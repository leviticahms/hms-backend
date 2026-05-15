"""
Schemas for Secure Result Access screen.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


AccessType = Literal["VIEW_ONLY", "DOWNLOAD", "SHARE"]


class ResultAccessStatCards(BaseModel):
    active_access: int = 0
    doctor_access: int = 0
    todays_accesses: int = 0
    mobile_accesses: int = 0


class ResultAccessPatientRow(BaseModel):
    """Rows for ``Patients with Result Access`` table (lab portal UI)."""

    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    patient_name: str
    email: str
    phone: str = Field(
        ...,
        description="Patient phone or literal ``N/A`` when not stored on the grant.",
    )
    last_access: str = Field(
        ...,
        description="Last access label (e.g. ``Never``) for dashboard display.",
    )
    access_count: int
    status: str = Field(
        ...,
        description="Grant status (typically ACTIVE, EXPIRED, REVOKED).",
    )
    access_code: str = Field(..., description="Share / verify code shown in the CODE column.")
    access_type: str = Field(
        default="VIEW_ONLY",
        description="VIEW_ONLY | DOWNLOAD | SHARE from grant.",
    )

    model_config = ConfigDict(populate_by_name=True)


class ResultAccessLogRow(BaseModel):
    """Rows for ``Recent Access Logs`` (patient, report type, device, IP)."""

    patient_ref: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    patient_name: str
    accessed_by: str
    access_time: str
    action: str
    report_type: str = Field(
        ...,
        description="Derived label for the REPORT TYPE column (from stored action when no DB column).",
    )
    ip_address: str
    device_browser: str

    model_config = ConfigDict(populate_by_name=True)


class ResultAccessMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class ResultAccessDashboardResponse(BaseModel):
    meta: ResultAccessMeta
    stats: ResultAccessStatCards
    patients: List[ResultAccessPatientRow] = Field(default_factory=list)
    access_logs: List[ResultAccessLogRow] = Field(default_factory=list)
    security_features: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GrantResultAccessRequest(BaseModel):
    patient_ref: str = Field(
        ...,
        min_length=3,
        max_length=40,
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    patient_name: Optional[str] = Field(
        None,
        max_length=120,
        description="Display name; defaults to patient_ref when omitted.",
    )
    email: str = Field(..., min_length=5, max_length=255)
    phone: Optional[str] = Field(
        None,
        max_length=30,
        description="Optional; shown in result-access dashboard (otherwise N/A).",
    )
    access_type: AccessType = "VIEW_ONLY"
    expiry_date: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class GrantResultAccessResponse(BaseModel):
    message: str
    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    email: str
    access_type: AccessType
    expiry_date: Optional[str] = None
    access_code: str

    model_config = ConfigDict(populate_by_name=True)

