"""Schemas for service/item master and tax profiles."""
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class TaxProfileCreate(BaseModel):
    name: str = Field(..., max_length=100)
    gst_percentage: float = Field(..., ge=0, le=100)
    is_active: bool = True


class TaxProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    gst_percentage: Optional[float] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None


class TaxProfileStatusPatch(BaseModel):
    """PATCH body for activate/deactivate tax profile."""

    is_active: bool


class ServiceItemStatusPatch(BaseModel):
    """PATCH body for activate/deactivate service item."""

    is_active: bool


class TaxProfileResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    name: str
    gst_percentage: float
    is_active: bool

    class Config:
        from_attributes = True


SERVICE_CATEGORIES = ("CONSULTATION", "PROCEDURE", "INVESTIGATION", "LAB", "PHARMACY", "BED", "PACKAGE", "MISC")


class ServiceItemCreate(BaseModel):
    department_id: Optional[UUID] = None
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    category: str = Field(..., max_length=50)
    base_price: float = Field(..., ge=0)
    tax_profile_id: Optional[UUID] = None
    is_active: bool = True


class ServiceItemUpdate(BaseModel):
    department_id: Optional[UUID] = None
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, max_length=255)
    category: Optional[str] = Field(None, max_length=50)
    base_price: Optional[float] = Field(None, ge=0)
    tax_profile_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class ServiceItemResponse(BaseModel):
    id: UUID
    hospital_id: UUID
    department_id: Optional[UUID]
    code: str
    name: str
    category: str
    base_price: float
    tax_profile_id: Optional[UUID]
    is_active: bool

    class Config:
        from_attributes = True
