from uuid import UUID
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import cloudinary.uploader

from app.models.tenant import Hospital
from app.models.user import User
from app.database.tenant_context import resolve_tenant_database_name_for_hospital
from app.database.session import get_tenant_session_factory

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2MB


async def upload_or_update_hospital_logo(
    *,
    hospital_id: UUID,
    file: UploadFile,
    current_user: User,
    platform_db: AsyncSession,
    allow_update: bool,
) -> str:
    """
    Upload hospital logo to Cloudinary and store logo_url
    in BOTH platform DB and tenant DB.
    """

    # SECURITY — ownership
    if hospital_id != current_user.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot modify another hospital's logo",
        )

    # ========================
    # PLATFORM DB
    # ========================
    hospital = await platform_db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    if hospital.logo_url and not allow_update:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Logo already exists. Use PUT to update.",
        )

    # ========================
    # FILE VALIDATION
    # ========================
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPEG, WEBP images allowed",
        )

    content = await file.read()
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Logo must be under 2MB",
        )

    # ========================
    # CLOUDINARY UPLOAD
    # ========================
    upload_result = cloudinary.uploader.upload(
        content,
        folder=f"hospital_logos/{hospital.name}",
        public_id="logo",
        overwrite=True,
        resource_type="image",
        transformation=[{"width": 300, "height": 300, "crop": "limit"}],
    )

    logo_url = upload_result["secure_url"]

    # ========================
    # SAVE → PLATFORM DB
    # ========================
    hospital.logo_url = logo_url
    await platform_db.commit()
    await platform_db.refresh(hospital)

    # ========================
    # SAVE → TENANT DB
    # ========================
    tenant_name = await resolve_tenant_database_name_for_hospital(hospital_id)

    if tenant_name:
        tenant_factory = get_tenant_session_factory(tenant_name)
        async with tenant_factory() as tenant_db:
            tenant_hospital = await tenant_db.get(Hospital, hospital_id)
            if tenant_hospital:
                tenant_hospital.logo_url = logo_url
                await tenant_db.commit()

    return logo_url


async def get_hospital_logo_url(
    *,
    hospital_id: UUID,
    current_user: User,
    db: AsyncSession,
) -> str:
    if hospital_id != current_user.hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot modify another hospital's logo",
        )

    hospital = await db.get(Hospital, hospital_id)
    if not hospital or not hospital.logo_url:
        raise HTTPException(status_code=404, detail="Logo not found")
    return hospital.logo_url

#doctor profile
async def upload_or_update_doctor_avatar(
    *,
    doctor_user_id: UUID,
    file: UploadFile,
    current_user: User,
    db: AsyncSession,
) -> str:
    # Fetch doctor user
    doctor = await db.get(User, doctor_user_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    #  Security checks
    if (
        doctor.id != current_user.id
        and doctor.hospital_id != current_user.hospital_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to update this doctor's profile photo",
        )

    # Validate file type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPEG, or WEBP images are allowed",
        )

    content = await file.read()

    # Validate size
    if len(content) > MAX_LOGO_SIZE:
        raise HTTPException(
            status_code=400,
            detail="Profile photo must be under 2MB",
        )

    # Upload to Cloudinary
    upload_result = cloudinary.uploader.upload(
        content,
        folder=f"doctor_avatars/{doctor_user_id}",
        public_id="avatar",
        overwrite=True,
        resource_type="image",
        transformation=[
            {"width": 300, "height": 300, "crop": "fill", "gravity": "face"}
        ],
    )

    avatar_url = upload_result["secure_url"]

    # Save URL in users table
    doctor.avatar_url = avatar_url
    await db.commit()
    await db.refresh(doctor)

    return avatar_url

async def get_doctor_avatar_url(
    *,
    doctor_user_id: UUID,
    current_user: User,
    db: AsyncSession,
) -> str:
    doctor = await db.get(User, doctor_user_id)
    if not doctor or not doctor.avatar_url:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return doctor.avatar_url