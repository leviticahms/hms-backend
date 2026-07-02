from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.core.utils import absolute_public_asset_url
from app.dependencies.auth import get_current_patient
from app.models.patient import PatientProfile
from app.schemas.patient import PatientProfileUpdate
from app.models.user import User
from app.services.logo import get_staff_avatar_url, upload_or_update_staff_avatar
from app.api.deps import (
    get_db_session,
    require_patient,)
router = APIRouter(prefix="/patient-profile", tags=["Patient Portal - Profile"])

PATIENT_PROFILE_UPDATE_FIELDS = {
    "date_of_birth",
    "gender",
    "address",
    "city",
    "district",
    "state",
    "country",
    "pincode",
    "mrn",
    "blood_group",
    "blood_group_value",
    "id_type",
    "id_number",
    "id_name",
    "emergency_contact_name",
    "emergency_contact_phone",
    "emergency_contact_relation",
    "medical_history",
    "allergies",
    "chronic_conditions",
    "current_medications",
    "insurance_provider",
    "insurance_policy_number",
    "insurance_expiry",
}
def should_update(value):
    if value is None:
        return False

    if isinstance(value, str):
        # ignore swagger default & empty strings
        if value.strip() == "" or value.strip().lower() == "string":
            return False

    return True

@router.get("/my/details")
async def get_my_details(
    current_patient: PatientProfile = Depends(get_current_patient),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    # ======================
    # PLATFORM USER
    # ======================
    user = await platform_db.get(User, current_patient.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ======================
    # TENANT PATIENT (already loaded)
    # ======================
    patient = current_patient  #  use this directly

    return {
        "patient_ref": patient.patient_id,

        # User fields
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "avatar_url": user.avatar_url,

        # Patient profile fields
        "mrn": patient.mrn,
        "date_of_birth": patient.date_of_birth,
        "gender": patient.gender,

        "address": patient.address,
        "city": patient.city,
        "district": patient.district,
        "state": patient.state,
        "country": patient.country,
        "pincode": patient.pincode,

        "blood_group": patient.blood_group,
        "blood_group_value": patient.blood_group_value,

        "id_type": patient.id_type,
        "id_number": patient.id_number,
        "id_name": patient.id_name,

        "emergency_contact_name": patient.emergency_contact_name,
        "emergency_contact_phone": patient.emergency_contact_phone,
        "emergency_contact_relation": patient.emergency_contact_relation,

        "medical_history": patient.medical_history,
        "allergies": patient.allergies,
        "chronic_conditions": patient.chronic_conditions,
        "current_medications": patient.current_medications,

        "insurance_provider": patient.insurance_provider,
        "insurance_policy_number": patient.insurance_policy_number,
        "insurance_expiry": patient.insurance_expiry,
    }


@router.patch("/my/details")
async def patch_my_details(
    payload: PatientProfileUpdate,
    current_patient: PatientProfile = Depends(get_current_patient),
):
    from app.database.session import get_tenant_session_factory
    from app.database.tenant_context import resolve_tenant_database_name_for_hospital

    data = payload.model_dump(exclude_unset=True)

    tenant_name = await resolve_tenant_database_name_for_hospital(
        current_patient.hospital_id
    )
    if not tenant_name:
        raise HTTPException(status_code=503, detail="Tenant DB not configured")

    factory = get_tenant_session_factory(str(tenant_name))
    async with factory() as tenant_db:
        result = await tenant_db.execute(
            select(PatientProfile).where(
                PatientProfile.user_id == current_patient.user_id
            )
        )
        patient = result.scalar_one_or_none()

        if not patient:
            raise HTTPException(status_code=404, detail="Patient profile not found")

        for field in PATIENT_PROFILE_UPDATE_FIELDS:
            if field in data and should_update(data[field]):
                setattr(patient, field, data[field])

        await tenant_db.commit()
        await tenant_db.refresh(patient)

    return {"message": "Patient profile updated successfully"}


@router.patch("/my/avatar")
async def patch_my_avatar(
    avatar: UploadFile = File(...),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    user_result = await db.execute(select(User).where(User.id == current_patient.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Metadata-only for now; frontend can keep existing upload pipeline.
    user.avatar_url = f"/uploads/avatars/{user.id}-{avatar.filename}"
    await db.commit()
    return {"avatar_url": absolute_public_asset_url(user.avatar_url)}


@router.get("/my/emergency-contacts")
async def get_my_emergency_contacts(
    current_patient: PatientProfile = Depends(get_current_patient),
):
    return {
        "contacts": [
            {
                "name": current_patient.emergency_contact_name,
                "relationship": current_patient.emergency_contact_relation,
                "phone": current_patient.emergency_contact_phone,
            }
        ]
        if current_patient.emergency_contact_name
        else []
    }


@router.get("/my/health-card")
async def get_my_health_card(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    result = await db.execute(
        select(PatientProfile).where(and_(PatientProfile.id == current_patient.id)).options(selectinload(PatientProfile.user))
    )
    patient = result.scalar_one()
    return {
        "patient_ref": patient.patient_id,
        "name": f"{patient.user.first_name} {patient.user.last_name}",
        "dob": patient.date_of_birth,
        "gender": patient.gender,
        "blood_group": patient.blood_group,
        "id_number": patient.id_number,
    }

@router.post(
    "/patient/me/avatar",
    tags=["Patient Portal - Profile"],
)
async def upload_patient_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(require_patient()),
    db: AsyncSession = Depends(get_db_session),
):
    avatar_url = await upload_or_update_staff_avatar(
        staff_user_id=current_user.id,
        role="patient",
        file=file,
        current_user=current_user,
        db=db,
        allow_update=False,  # POST = create only
    )

    return {
        "success": True,
        "message": "Profile photo uploaded successfully",
        "avatar_url": avatar_url,
    }
@router.put(
    "/patient/me/avatar",
    tags=["Patient Portal - Profile"],
)
async def update_patient_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(require_patient()),
    db: AsyncSession = Depends(get_db_session),
):
    avatar_url = await upload_or_update_staff_avatar(
        staff_user_id=current_user.id,
        role="patient",
        file=file,
        current_user=current_user,
        db=db,
        allow_update=True,   # PUT = overwrite
    )

    return {
        "success": True,
        "message": "Profile photo updated successfully",
        "avatar_url": avatar_url,
    }
@router.get(
    "/patient/me/avatar",
    tags=["Patient Portal - Profile"],
)
async def get_patient_avatar(
    current_user: User = Depends(require_patient()),
    db: AsyncSession = Depends(get_db_session),
):
    avatar_url = await get_staff_avatar_url(
        staff_user_id=current_user.id,
        current_user=current_user,
        db=db,
    )

    return {
        "success": True,
        "avatar_url": avatar_url,
    }