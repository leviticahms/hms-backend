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

#--------------------------
#staff avatar upload
#--------------------------


async def upload_or_update_staff_avatar(
    *,
    staff_user_id,
    role: str,  # doctor | nurse | receptionist | patient
    file: UploadFile,
    current_user: User,
    db: AsyncSession,
    allow_update: bool,   # 🔑 POST=False, PUT=True
) -> str:
    #  OWNERSHIP
    if staff_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can update only your own profile photo",
        )

    user = await db.get(User, staff_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    #  Prevent POST overwrite
    if user.avatar_url and not allow_update:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Profile photo already exists. Go to update.",
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
            detail="Avatar must be under 2MB",
        )

    # ========================
    # CLOUDINARY UPLOAD
    # ========================
    upload_result = cloudinary.uploader.upload(
        content,
        folder="staff_profiles",               # ONE folder
        public_id=f"{role}_{staff_user_id}",   # visible file name
        overwrite=True,                        # PUT replaces
        resource_type="image",
        transformation=[
            {"width": 300, "height": 300, "crop": "fill", "gravity": "face"}
        ],
    )

    avatar_url = upload_result["secure_url"]

    # ========================
    # SAVE
    # ========================
    user.avatar_url = avatar_url
    await db.commit()
    await db.refresh(user)

    return avatar_url
async def get_staff_avatar_url(
    *,
    staff_user_id,
    current_user: User,
    db: AsyncSession,
) -> str:
    #  OWNERSHIP
    if staff_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can view only your own profile photo",
        )

    user = await db.get(User, staff_user_id)
    if not user or not user.avatar_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile photo not found",
        )

    return user.avatar_url