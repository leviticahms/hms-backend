from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_platform_db_session
from app.core.utils import absolute_public_asset_url
from app.dependencies.auth import get_current_patient
from app.models.patient import PatientProfile
from app.models.user import User
from app.services.logo import get_staff_avatar_url, upload_or_update_staff_avatar
from app.api.deps import (
    get_db_session,
    require_patient,)
router = APIRouter(prefix="/patient-profile", tags=["Patient Portal - Profile"])


@router.get("/my/details")
async def get_my_details(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    user_result = await db.execute(select(User).where(User.id == current_patient.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "patient_ref": current_patient.patient_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "date_of_birth": current_patient.date_of_birth,
        "gender": current_patient.gender,
        "address": current_patient.address,
        "city": current_patient.city,
        "state": current_patient.state,
        "country": current_patient.country,
        "pincode": current_patient.pincode,
        "avatar_url": absolute_public_asset_url(user.avatar_url),
    }


@router.patch("/my/details")
async def patch_my_details(
    payload: dict,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_platform_db_session),
):
    user_result = await db.execute(select(User).where(User.id == current_patient.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for field in ("first_name", "last_name", "email", "phone"):
        if field in payload:
            setattr(user, field, payload[field])
    for field in (
        "date_of_birth",
        "gender",
        "address",
        "city",
        "district",
        "state",
        "country",
        "pincode",
        "emergency_contact_name",
        "emergency_contact_phone",
        "emergency_contact_relation",
    ):
        if field in payload:
            setattr(current_patient, field, payload[field])
    await db.commit()
    return {"message": "Profile updated successfully"}


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
        role="patient",
        current_user=current_user,
        db=db,
    )

    return {
        "success": True,
        "avatar_url": avatar_url,
    }