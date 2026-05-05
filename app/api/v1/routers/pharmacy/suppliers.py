"""Supplier Management Router - Complete CRUD operations"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID
from app.database.session import get_db_session
from app.dependencies.auth import require_hospital_admin
from app.models.user import User
from app.services.pharmacy_service import PharmacyService
from app.schemas.pharmacy_suppliers_crud import SupplierCreate, SupplierUpdate
from app.schemas.response import SuccessResponse

router = APIRouter(prefix="/suppliers", tags=["Pharmacy - Suppliers"])


@router.get("")
async def list_suppliers(
    status: Optional[str] = Query(None, description="Filter by status (ACTIVE, INACTIVE)"),
    search: Optional[str] = Query(None, description="Search by name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """List all suppliers"""
    service = PharmacyService(db)
    suppliers = await service.get_suppliers(current_user.hospital_id, status)
    
    return SuccessResponse(
        success=True,
        message=f"Found {len(suppliers)} suppliers",
        data={
            "suppliers": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "contact_person": s.contact_person,
                    "phone": s.phone,
                    "email": s.email,
                    "city": s.city,
                    "state": s.state,
                    "status": s.status,
                    "rating": s.rating,
                    "payment_terms": s.payment_terms,
                    "created_at": str(s.created_at)
                }
                for s in suppliers
            ],
            "total": len(suppliers),
            "skip": skip,
            "limit": limit
        }
    ).dict()


@router.get("/{supplier_id}")
async def get_supplier(
    supplier_id: UUID,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """Get supplier details by ID"""
    service = PharmacyService(db)
    supplier = await service.get_supplier(supplier_id, current_user.hospital_id)
    
    return SuccessResponse(
        success=True,
        message="Supplier retrieved successfully",
        data={
            "supplier": {
                "id": str(supplier.id),
                "name": supplier.name,
                "contact_person": supplier.contact_person,
                "phone": supplier.phone,
                "email": supplier.email,
                "address_line1": supplier.address_line1,
                "address_line2": supplier.address_line2,
                "city": supplier.city,
                "state": supplier.state,
                "pincode": supplier.pincode,
                "country": supplier.country,
                "gstin": supplier.gstin,
                "drug_license_no": supplier.drug_license_no,
                "payment_terms": supplier.payment_terms,
                "credit_limit": float(supplier.credit_limit) if supplier.credit_limit else None,
                "rating": supplier.rating,
                "status": supplier.status,
                "notes": supplier.notes,
                "created_at": str(supplier.created_at),
                "updated_at": str(supplier.updated_at)
            }
        }
    ).dict()


@router.post("")
async def create_supplier(
    supplier_data: SupplierCreate,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """Create a new supplier"""
    service = PharmacyService(db)
    supplier = await service.create_supplier(
        hospital_id=current_user.hospital_id,
        **supplier_data.model_dump()
    )
    await db.commit()
    
    return SuccessResponse(
        success=True,
        message="Supplier created successfully",
        data={"supplier_id": str(supplier.id)}
    ).dict()


@router.put("/{supplier_id}")
async def update_supplier(
    supplier_id: UUID,
    supplier_data: SupplierUpdate,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """Update supplier details"""
    service = PharmacyService(db)
    updates = {k: v for k, v in supplier_data.model_dump().items() if v is not None}
    supplier = await service.update_supplier(
        supplier_id=supplier_id,
        hospital_id=current_user.hospital_id,
        **updates
    )
    await db.commit()
    
    return SuccessResponse(
        success=True,
        message="Supplier updated successfully",
        data={"supplier_id": str(supplier.id)}
    ).dict()


@router.delete("/{supplier_id}")
async def delete_supplier(
    supplier_id: UUID,
    current_user: User = Depends(require_hospital_admin()),
    db: AsyncSession = Depends(get_db_session)
):
    """Soft delete a supplier"""
    service = PharmacyService(db)
    supplier = await service.get_supplier(supplier_id, current_user.hospital_id)
    supplier.is_active = False
    await db.commit()
    
    return SuccessResponse(
        success=True,
        message="Supplier deleted successfully",
        data={"supplier_id": str(supplier_id)}
    ).dict()
