"""Resolve effective feature flags for a hospital from platform `subscription_plans`."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plan_features import (
    ALL_FEATURE_KEYS,
    DEFAULT_FEATURES_BY_PLAN,
    normalize_plan_name,
)
from app.models.tenant import Hospital, HospitalSubscription, SubscriptionPlanModel
from app.schemas.plan_features import FEATURE_MODULE_LABELS


async def _load_plan_row(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Optional[SubscriptionPlanModel]:
    r = await db.execute(
        select(SubscriptionPlanModel)
        .join(HospitalSubscription, HospitalSubscription.plan_id == SubscriptionPlanModel.id)
        .where(HospitalSubscription.hospital_id == hospital_id)
    )
    return r.scalar_one_or_none()


def _merge_features(plan: Optional[SubscriptionPlanModel]) -> Dict[str, bool]:
    if not plan:
        return dict(DEFAULT_FEATURES_BY_PLAN["STANDARD"])
    pname = normalize_plan_name(plan.name)
    base = dict(DEFAULT_FEATURES_BY_PLAN.get(pname, DEFAULT_FEATURES_BY_PLAN["STANDARD"]))
    ov = plan.features if isinstance(plan.features, dict) else {}
    for k in ALL_FEATURE_KEYS:
        if k in ov:
            base[k] = bool(ov[k])
    return base


async def get_effective_feature_map(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Dict[str, bool]:
    plan = await _load_plan_row(db, hospital_id)
    return _merge_features(plan)


async def get_plan_info_for_hospital(
    db: AsyncSession, hospital_id: uuid.UUID
) -> Tuple[Optional[str], Optional[str], Dict[str, bool]]:
    plan = await _load_plan_row(db, hospital_id)
    feats = _merge_features(plan)
    if not plan:
        return None, None, feats
    return plan.name, plan.display_name, feats


async def get_hospital_platform_settings_bundle(
    db: AsyncSession, hospital_id: uuid.UUID
) -> dict:
    """
    Registry + subscription summary for Hospital Admin "Platform Settings" UI.
    Caller must use platform AsyncSession.
    """
    hosp = await db.get(Hospital, hospital_id)
    sub = (
        await db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == hospital_id)
        )
    ).scalar_one_or_none()
    pname, display, feats = await get_plan_info_for_hospital(db, hospital_id)
    end_iso = None
    if sub and sub.end_date:
        end_iso = sub.end_date.isoformat() if hasattr(sub.end_date, "isoformat") else str(sub.end_date)
    return {
        "hospital_id": str(hospital_id),
        "hospital_name": hosp.name if hosp else None,
        "tenant_database_name": (hosp.tenant_database_name if hosp else None) or None,
        "subscription_status": sub.status if sub else None,
        "subscription_end_date": end_iso,
        "plan_name": pname,
        "plan_display_name": display,
        "features": feats,
    }


async def is_feature_enabled(
    db: AsyncSession, hospital_id: uuid.UUID, feature_key: str
) -> bool:
    plan = await _load_plan_row(db, hospital_id)
    m = _merge_features(plan)
    return bool(m.get(feature_key, False))


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _decimal_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def get_hospital_subscription_detail_bundle(
    db: AsyncSession, hospital_id: uuid.UUID
) -> dict:
    sub = (
        await db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == hospital_id)
        )
    ).scalar_one_or_none()
    if not sub:
        return {
            "hospital_id": str(hospital_id),
            "plan_id": None,
            "status": None,
            "start_date": None,
            "end_date": None,
            "is_trial": None,
            "trial_end_date": None,
            "auto_renew": None,
            "current_usage": {},
        }
    return {
        "hospital_id": str(hospital_id),
        "plan_id": str(sub.plan_id),
        "status": sub.status,
        "start_date": _iso(sub.start_date),
        "end_date": _iso(sub.end_date),
        "is_trial": sub.is_trial,
        "trial_end_date": _iso(sub.trial_end_date),
        "auto_renew": sub.auto_renew,
        "current_usage": dict(sub.current_usage or {}),
    }


async def get_hospital_plan_quotas_bundle(
    db: AsyncSession, hospital_id: uuid.UUID
) -> dict:
    plan = await _load_plan_row(db, hospital_id)
    if not plan:
        return {
            "plan_id": None,
            "plan_name": None,
            "plan_display_name": None,
            "description": None,
            "monthly_price": None,
            "yearly_price": None,
            "max_doctors": None,
            "max_patients": None,
            "max_appointments_per_month": None,
            "max_storage_gb": None,
            "unlimited_doctors": None,
            "unlimited_patients": None,
            "plan_features_json": {},
        }
    md, mp = plan.max_doctors, plan.max_patients
    return {
        "plan_id": str(plan.id),
        "plan_name": plan.name,
        "plan_display_name": plan.display_name,
        "description": plan.description,
        "monthly_price": _decimal_to_float(plan.monthly_price),
        "yearly_price": _decimal_to_float(plan.yearly_price),
        "max_doctors": md,
        "max_patients": mp,
        "max_appointments_per_month": plan.max_appointments_per_month,
        "max_storage_gb": plan.max_storage_gb,
        "unlimited_doctors": md == 0,
        "unlimited_patients": mp == 0,
        "plan_features_json": dict(plan.features or {}),
    }


async def get_hospital_registry_platform_bundle(
    db: AsyncSession, hospital_id: uuid.UUID
) -> dict:
    hosp = await db.get(Hospital, hospital_id)
    if not hosp:
        return {
            "hospital_id": str(hospital_id),
            "name": None,
            "registration_number": None,
            "email": None,
            "phone": None,
            "address": None,
            "city": None,
            "state": None,
            "country": None,
            "pincode": None,
            "license_number": None,
            "established_date": None,
            "website": None,
            "logo_url": None,
            "is_active": None,
            "status": None,
            "tenant_database_name": None,
            "settings": {},
        }
    return {
        "hospital_id": str(hospital_id),
        "name": hosp.name,
        "registration_number": hosp.registration_number,
        "email": hosp.email,
        "phone": hosp.phone,
        "address": hosp.address,
        "city": hosp.city,
        "state": hosp.state,
        "country": hosp.country,
        "pincode": hosp.pincode,
        "license_number": hosp.license_number,
        "established_date": _iso(hosp.established_date),
        "website": hosp.website,
        "logo_url": hosp.logo_url,
        "is_active": hosp.is_active,
        "status": hosp.status,
        "tenant_database_name": hosp.tenant_database_name,
        "settings": dict(hosp.settings or {}),
    }


async def get_hospital_modules_bundle(db: AsyncSession, hospital_id: uuid.UUID) -> dict:
    feats = await get_effective_feature_map(db, hospital_id)
    modules: List[dict] = []
    for key in sorted(ALL_FEATURE_KEYS):
        label, desc = FEATURE_MODULE_LABELS.get(key, (key.replace("_", " ").title(), ""))
        modules.append(
            {"key": key, "label": label, "description": desc, "enabled": bool(feats.get(key, False))}
        )
    return {"modules": modules}


async def get_hospital_usage_vs_limits_bundle(db: AsyncSession, hospital_id: uuid.UUID) -> dict:
    sub = (
        await db.execute(
            select(HospitalSubscription).where(HospitalSubscription.hospital_id == hospital_id)
        )
    ).scalar_one_or_none()
    plan = await _load_plan_row(db, hospital_id)
    usage = dict(sub.current_usage or {}) if sub else {}
    if not plan:
        return {"current_usage": usage, "limits": {}}
    md, mp = plan.max_doctors, plan.max_patients
    limits = {
        "max_doctors": md,
        "max_patients": mp,
        "max_appointments_per_month": plan.max_appointments_per_month,
        "max_storage_gb": plan.max_storage_gb,
        "unlimited_doctors": md == 0,
        "unlimited_patients": mp == 0,
    }
    return {"current_usage": usage, "limits": limits}
