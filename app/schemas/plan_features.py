"""Schemas for subscription feature flags (dashboard / modules)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.plan_features import (
    FEATURE_LAB_TESTS,
    FEATURE_PHARMACY,
    FEATURE_VIDEO_CONSULTATION,
)

# Stable labels for Hospital Admin "Platform settings" module list UI.
FEATURE_MODULE_LABELS: Dict[str, tuple[str, str]] = {
    FEATURE_LAB_TESTS: ("Lab tests", "Laboratory workflows and reports"),
    FEATURE_VIDEO_CONSULTATION: ("Video consultation", "Telemedicine visits"),
    FEATURE_PHARMACY: ("Pharmacy", "Inventory, dispensing, and sales"),
}


class HospitalFeatureFlagsOut(BaseModel):
    plan_name: Optional[str] = Field(None, description="Raw plan name from subscription_plans.name")
    plan_display_name: Optional[str] = None
    features: Dict[str, bool] = Field(
        ...,
        description="lab_tests, video_consultation, pharmacy — for UI module toggles",
    )


class HospitalPlatformSettingsOut(BaseModel):
    """Hospital Admin — platform registry + subscription (always read from platform DB)."""

    hospital_id: str = Field(..., description="Hospital UUID")
    hospital_name: Optional[str] = None
    tenant_database_name: Optional[str] = Field(
        None, description="Dedicated DB name when multi-DB routing is enabled"
    )
    subscription_status: Optional[str] = Field(None, description="HospitalSubscription.status")
    subscription_end_date: Optional[str] = Field(None, description="ISO8601 end_date when present")
    plan_name: Optional[str] = None
    plan_display_name: Optional[str] = None
    features: Dict[str, bool] = Field(
        default_factory=dict,
        description="Effective feature flags from plan + overrides",
    )


class HospitalSubscriptionDetailOut(BaseModel):
    """Platform subscription row for the authenticated hospital (read-only)."""

    hospital_id: str
    plan_id: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[str] = Field(None, description="ISO8601 when present")
    end_date: Optional[str] = None
    is_trial: Optional[bool] = None
    trial_end_date: Optional[str] = None
    auto_renew: Optional[bool] = None
    current_usage: Dict[str, Any] = Field(default_factory=dict)


class HospitalPlanQuotasOut(BaseModel):
    """Plan tier limits and pricing from `subscription_plans` (read-only)."""

    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    plan_display_name: Optional[str] = None
    description: Optional[str] = None
    monthly_price: Optional[float] = None
    yearly_price: Optional[float] = None
    max_doctors: Optional[int] = None
    max_patients: Optional[int] = None
    max_appointments_per_month: Optional[int] = None
    max_storage_gb: Optional[int] = None
    unlimited_doctors: Optional[bool] = Field(
        None, description="True when max_doctors is 0 (unlimited per model convention)"
    )
    unlimited_patients: Optional[bool] = Field(
        None, description="True when max_patients is 0 (unlimited per model convention)"
    )
    plan_features_json: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw `subscription_plans.features` overrides",
    )


class HospitalRegistryPlatformOut(BaseModel):
    """Hospital row from platform registry (`hospitals`) for settings screens."""

    hospital_id: str
    name: Optional[str] = None
    registration_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    license_number: Optional[str] = None
    established_date: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None
    tenant_database_name: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class PlatformModuleItemOut(BaseModel):
    key: str
    label: str
    description: str
    enabled: bool


class HospitalModulesOut(BaseModel):
    modules: List[PlatformModuleItemOut] = Field(
        ...,
        description="One entry per canonical feature key with UI label",
    )


class HospitalUsageVsLimitsOut(BaseModel):
    current_usage: Dict[str, Any] = Field(default_factory=dict)
    limits: Dict[str, Any] = Field(
        default_factory=dict,
        description="Plan quotas; empty when no plan is linked",
    )
