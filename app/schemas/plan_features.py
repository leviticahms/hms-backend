"""Schemas for subscription feature flags (dashboard / modules)."""
from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field


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
