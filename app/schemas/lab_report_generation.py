"""
Schemas for Lab Report Generation screen.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


ReportTemplate = Literal["STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"]
ReportStatus = Literal["READY", "PENDING_REVIEW", "DRAFT"]

_REPORT_TEMPLATES = frozenset(
    {"STANDARD", "COMPREHENSIVE", "DOCTOR_SUMMARY", "PATIENT_FRIENDLY", "CUSTOM"}
)


class ReportGenerationRow(BaseModel):
    report_id: str
    patient_name: str
    test_type: str
    completion_date: date
    status: ReportStatus
    verified_by: Optional[str] = None


class ReportGenerationSummary(BaseModel):
    total_reports: int = 0
    ready_reports: int = 0
    pending_review: int = 0
    test_types: int = 0


class ReportGenerationMeta(BaseModel):
    generated_at: datetime
    live_data: bool = False
    demo_data: bool = False


class ReportGenerationListResponse(BaseModel):
    meta: ReportGenerationMeta
    selected_template: ReportTemplate
    templates: List[ReportTemplate] = Field(default_factory=list)
    summary: ReportGenerationSummary
    rows: List[ReportGenerationRow] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ReadyTestForReportRow(BaseModel):
    patient_name: str
    patient_ref: str = Field(
        validation_alias=AliasChoices("patient_ref", "patient_id"),
        serialization_alias="patient_ref",
    )
    test_type: str
    completed_on: date
    source_test_id: str

    model_config = ConfigDict(populate_by_name=True)


class ReadyTestsResponse(BaseModel):
    rows: List[ReadyTestForReportRow] = Field(default_factory=list)


class GenerateReportRequest(BaseModel):
    """Body fields match Swagger; UIs often send ``testId`` / ``test_id`` instead of ``source_test_id``."""

    source_test_id: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices(
            "source_test_id",
            "sourceTestId",
            "test_id",
            "testId",
        ),
        serialization_alias="source_test_id",
        description="``lab_report_ready_tests.source_test_id`` or ``lab_test_registrations.test_id`` (REG-...), or registration row UUID",
    )
    template: ReportTemplate = "STANDARD"

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("template", mode="before")
    @classmethod
    def _normalize_template(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "STANDARD"
        s = str(v).strip().upper()
        return s if s in _REPORT_TEMPLATES else "STANDARD"


class UpdateReportGenerationRequest(BaseModel):
    """Partial update for an existing generated report row."""

    patient_name: Optional[str] = None
    patient_ref: Optional[str] = None
    doctor_name: Optional[str] = None
    test_type: Optional[str] = None
    completion_date: Optional[date] = None
    status: Optional[ReportStatus] = None
    verified_by: Optional[str] = None
    template: Optional[ReportTemplate] = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("template", mode="before")
    @classmethod
    def _normalize_template_opt(cls, v: object) -> Optional[str]:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        s = str(v).strip().upper()
        if s not in _REPORT_TEMPLATES:
            raise ValueError(
                f"Invalid template {v!r}; allowed: {', '.join(sorted(_REPORT_TEMPLATES))}"
            )
        return s


class UpdateReportGenerationResponse(BaseModel):
    message: str
    report_id: str


class GenerateReportResponse(BaseModel):
    message: str
    report_id: str
    status: ReportStatus


class ReportPreviewResponse(BaseModel):
    report_id: str
    title: str
    patient_name: str
    test_type: str
    status: ReportStatus
    template: ReportTemplate
    preview_text: str


class PrintReportResponse(BaseModel):
    message: str
    report_id: str
    print_job_id: str

