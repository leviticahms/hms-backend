"""Returns Router - Patient & Supplier returns"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import Optional
from uuid import UUID, uuid4

from app.core.enums import UserRole
from app.database.session import get_db_session
from app.dependencies.auth import require_pharmacy_staff, require_hospital_context
from app.models.user import User, Role, user_roles
from app.services.pharmacy_service import PharmacyService
from app.schemas.pharmacy import PatientReturnCreate, ReturnOut, SupplierReturnCreate
from app.schemas.response import SuccessResponse

router = APIRouter(prefix="/returns", tags=["Pharmacy - Returns"])


async def ensure_current_user_in_tenant_db(db: AsyncSession, current_user: User) -> None:
    """Keep tenant-side user FKs valid when auth resolves from the platform DB."""
    data = {column.name: getattr(current_user, column.name) for column in User.__table__.columns}
    existing_user = await db.get(User, current_user.id)
    if existing_user:
        for key, value in data.items():
            if key != "id":
                setattr(existing_user, key, value)
    else:
        db.add(User(**data))
    await db.flush()

    loaded_roles = current_user.__dict__.get("roles") or []
    role_names = [getattr(role, "name", None) for role in loaded_roles if getattr(role, "name", None)]
    if not role_names:
        role_names = [UserRole.PHARMACIST.value]

    for role_name in role_names:
        role_result = await db.execute(select(Role).where(Role.name == role_name))
        tenant_role = role_result.scalar_one_or_none()
        if not tenant_role:
            tenant_role = Role(
                id=uuid4(),
                name=role_name,
                display_name=role_name.replace("_", " ").title(),
                description="Mirrored for tenant pharmacy returns",
                is_system_role=True,
                level=50,
            )
            db.add(tenant_role)
            await db.flush()
        await db.execute(
            pg_insert(user_roles)
            .values(user_id=current_user.id, role_id=tenant_role.id)
            .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
        )
    await db.flush()


@router.post("/patient", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_patient_return(
    return_data: PatientReturnCreate,
    current_user: User = Depends(require_pharmacy_staff()),
    db: AsyncSession = Depends(get_db_session)
):
    """Patient return - increments stock"""
    await ensure_current_user_in_tenant_db(db, current_user)
    service = PharmacyService(db)
    return_record = await service.create_patient_return(
        hospital_id=current_user.hospital_id,
        returned_by=current_user.id,
        **return_data.dict()
    )
    await db.commit()
    return SuccessResponse(success=True, message="Patient return created", data={"return_id": str(return_record.id)}).dict()


@router.post("/supplier", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_supplier_return(
    return_data: SupplierReturnCreate,
    current_user: User = Depends(require_pharmacy_staff()),
    db: AsyncSession = Depends(get_db_session)
):
    """Supplier return - decrements stock"""
    await ensure_current_user_in_tenant_db(db, current_user)
    service = PharmacyService(db)
    return_record = await service.create_supplier_return(
        hospital_id=current_user.hospital_id,
        returned_by=current_user.id,
        **return_data.dict()
    )
    await db.commit()
    return SuccessResponse(success=True, message="Supplier return created", data={"return_id": str(return_record.id)}).dict()


@router.get("", response_model=dict)
async def list_returns(
    return_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    context: dict = Depends(require_hospital_context),
    db: AsyncSession = Depends(get_db_session)
):
    """List returns"""
    service = PharmacyService(db)
    returns_list = await service.get_returns(UUID(context["hospital_id"]), return_type, skip, limit)
    returns_data = [
        ReturnOut.model_validate(return_record).model_dump(mode="json")
        for return_record in returns_list
    ]
    return SuccessResponse(success=True, message=f"Found {len(returns_list)} returns", data={"returns": returns_data}).dict()
