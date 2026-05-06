"""
Patient Document Upload and Storage API
Secure document management for patient files, medical reports, and attachments.
"""
import os
import uuid
import aiofiles
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.dependencies.auth import get_current_patient
from app.core.config import settings
from app.models.user import User
from app.models.patient import PatientProfile, PatientDocument
from app.models.doctor import DoctorProfile
from app.core.enums import UserRole, DocumentType
from app.core.utils import generate_patient_ref
from app.schemas.patient_care import DocumentUploadOut, DocumentListOut, DocumentUpdate

router = APIRouter(prefix="/patient-document-storage", tags=["Patient Portal - Document Storage"])


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    
    return {
        "user_id": str(current_user.id),  # Convert to string for consistent comparison
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": user_roles[0] if user_roles else None,
        "all_roles": user_roles
    }


async def check_doctor_patient_access(doctor_user_id: str, patient_id: str, db: AsyncSession) -> bool:
    """
    Check if a doctor has access to a patient's records.
    Returns True if doctor has treated the patient, False otherwise.
    """
    # Get doctor profile
    doctor_result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == doctor_user_id)
    )
    doctor = doctor_result.scalar_one_or_none()
    
    if not doctor:
        return False
    
    # Check if doctor has medical records for this patient
    from app.models.patient import MedicalRecord
    record_check = await db.execute(
        select(MedicalRecord)
        .where(
            and_(
                MedicalRecord.patient_id == patient_id,
                MedicalRecord.doctor_id == doctor.id
            )
        )
        .limit(1)
    )
    
    return record_check.scalar_one_or_none() is not None


async def get_patient_by_ref(patient_ref: str, hospital_id: Optional[str], db: AsyncSession) -> PatientProfile:
    """Get patient by reference with hospital isolation"""
    # First try with hospital_id if provided
    if hospital_id:
        result = await db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == hospital_id
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        patient = result.scalar_one_or_none()
        if patient:
            return patient
    
    # If not found or hospital_id is None, try without hospital_id filter
    # This handles cases where patient's hospital_id is null
    result = await db.execute(
        select(PatientProfile)
        .where(PatientProfile.patient_id == patient_ref)
        .options(selectinload(PatientProfile.user))
    )
    
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient {patient_ref} not found"
        )
    
    return patient


def get_upload_directory(hospital_id: str, patient_ref: str) -> str:
    """Get secure upload directory for patient documents"""
    # Create hospital-specific directory structure
    upload_dir = os.path.join(
        settings.UPLOAD_DIR or "uploads",
        "hospitals",
        hospital_id,
        "patients",
        patient_ref,
        "documents"
    )
    
    # Create directory if it doesn't exist
    os.makedirs(upload_dir, exist_ok=True)
    
    return upload_dir


def validate_file_type(file: UploadFile) -> bool:
    """Validate uploaded file type"""
    allowed_types = {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/gif",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    
    return file.content_type in allowed_types


def validate_file_size(file: UploadFile, max_size_mb: int = 10) -> bool:
    """Validate file size (default 10MB limit)"""
    if not hasattr(file, 'size') or file.size is None:
        return True  # Cannot determine size, allow upload
    
    max_size_bytes = max_size_mb * 1024 * 1024
    return file.size <= max_size_bytes


async def save_uploaded_file(file: UploadFile, file_path: str) -> int:
    """Save uploaded file to disk and return file size"""
    file_size = 0
    
    async with aiofiles.open(file_path, 'wb') as f:
        while chunk := await file.read(8192):  # Read in 8KB chunks
            await f.write(chunk)
            file_size += len(chunk)
    
    return file_size


# ============================================================================
# PATIENT SELF-SERVICE ENDPOINTS (no patient_ref needed - from JWT token)
# ============================================================================

@router.post("/my/documents/upload")
async def upload_my_document(
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    is_sensitive: bool = Form(True),
    current_patient: PatientProfile = Depends(get_current_patient),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Upload a document for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own documents from JWT token)
    """
    patient = current_patient
    user_context = get_user_context(current_user)
    
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
        )
    
    if not validate_file_type(file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Allowed types: PDF, Images (JPEG, PNG, GIF), Word documents, Excel files, Text files"
        )
    
    if not validate_file_size(file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10MB limit"
        )
    
    hospital_id_for_doc = str(patient.hospital_id) if patient.hospital_id else user_context.get("hospital_id")
    if not hospital_id_for_doc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Please book an appointment first to link your account to a hospital."
        )
    
    patient_ref = patient.patient_id
    upload_dir = get_upload_directory(hospital_id_for_doc, patient_ref)
    unique_filename = f"{uuid.uuid4()}{os.path.splitext(file.filename)[1] if file.filename else ''}"
    file_path = os.path.join(upload_dir, unique_filename)
    
    try:
        file_size = await save_uploaded_file(file, file_path)
        
        document = PatientDocument(
            id=uuid.uuid4(),
            hospital_id=uuid.UUID(hospital_id_for_doc),
            patient_id=patient.id,
            uploaded_by=current_user.id,
            document_type=document_type,
            title=title,
            description=description,
            file_name=file.filename or unique_filename,
            file_path=file_path,
            file_size=file_size,
            mime_type=file.content_type,
            document_date=document_date,
            is_sensitive=is_sensitive
        )
        
        db.add(document)
        await db.commit()
        
        return DocumentUploadOut(
            document_id=str(document.id),
            patient_ref=patient_ref,
            document_type=document_type,
            title=title,
            file_name=file.filename or unique_filename,
            file_size=file_size,
            upload_date=document.created_at.isoformat(),
            message="Document uploaded successfully"
        )
        
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload document: {str(e)}"
        )


@router.get("/my/documents/statistics")
async def get_my_document_statistics(
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get document statistics for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own documents from JWT token)
    """
    patient = current_patient
    
    total_result = await db.execute(
        select(func.count(PatientDocument.id)).where(PatientDocument.patient_id == patient.id)
    )
    total_documents = total_result.scalar() or 0
    
    by_type_result = await db.execute(
        select(PatientDocument.document_type, func.count(PatientDocument.id))
        .where(PatientDocument.patient_id == patient.id)
        .group_by(PatientDocument.document_type)
    )
    by_type = {row[0]: row[1] for row in by_type_result.fetchall()}
    
    return {
        "patient_ref": patient.patient_id,
        "total_documents": total_documents,
        "by_type": by_type
    }


@router.get("/my/documents")
async def get_my_documents(
    document_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get paginated list of documents for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own documents from JWT token)
    """
    patient = current_patient
    offset = (page - 1) * limit
    
    query = select(PatientDocument).where(
        PatientDocument.patient_id == patient.id
    ).options(
        selectinload(PatientDocument.uploader)
    ).order_by(desc(PatientDocument.created_at))
    
    if document_type:
        try:
            DocumentType(document_type)
            query = query.where(PatientDocument.document_type == document_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
            )
    
    count_query = select(func.count(PatientDocument.id)).where(
        PatientDocument.patient_id == patient.id
    )
    if document_type:
        count_query = count_query.where(PatientDocument.document_type == document_type)
    
    total_result = await db.execute(count_query)
    total_documents = total_result.scalar() or 0
    
    documents_result = await db.execute(query.offset(offset).limit(limit))
    documents = documents_result.scalars().all()
    
    document_list = [
        DocumentListOut(
            document_id=str(doc.id),
            document_type=doc.document_type,
            title=doc.title,
            description=doc.description,
            file_name=doc.file_name,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            document_date=doc.document_date,
            upload_date=doc.created_at.isoformat(),
            uploaded_by=f"{doc.uploader.first_name} {doc.uploader.last_name}",
            is_sensitive=doc.is_sensitive
        )
        for doc in documents
    ]
    
    return {
        "patient_ref": patient.patient_id,
        "documents": document_list,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_documents,
            "pages": (total_documents + limit - 1) // limit
        }
    }


@router.get("/my/documents/{document_id}")
async def get_my_document_details(
    document_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed information about a specific document for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own documents from JWT token)
    """
    patient = current_patient
    
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
        .options(selectinload(PatientDocument.uploader))
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    return {
        "document_id": str(document.id),
        "patient_ref": patient.patient_id,
        "document_type": document.document_type,
        "title": document.title,
        "description": document.description,
        "file_name": document.file_name,
        "file_size": document.file_size,
        "mime_type": document.mime_type,
        "document_date": document.document_date,
        "is_sensitive": document.is_sensitive,
        "upload_date": document.created_at.isoformat(),
        "uploaded_by": f"{document.uploader.first_name} {document.uploader.last_name}",
        "uploader_role": [role.name for role in document.uploader.roles][0] if document.uploader.roles else "Unknown"
    }


@router.get("/my/documents/{document_id}/download")
async def download_my_document(
    document_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Download a document for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (own documents from JWT token)
    """
    patient = current_patient
    
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk"
        )
    
    return FileResponse(
        path=document.file_path,
        filename=document.file_name,
        media_type=document.mime_type or 'application/octet-stream'
    )


@router.patch("/my/documents/{document_id}")
async def update_my_document_metadata(
    document_id: str,
    update_data: DocumentUpdate,
    current_patient: PatientProfile = Depends(get_current_patient),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update document metadata for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (documents they uploaded, from JWT token)
    """
    patient = current_patient
    user_context = get_user_context(current_user)
    
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    if document.uploaded_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - can only update documents you uploaded"
        )
    
    if update_data.document_type:
        try:
            DocumentType(update_data.document_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
            )
    
    update_fields = update_data.dict(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(document, field, value)
    
    await db.commit()
    
    return {
        "document_id": str(document.id),
        "message": "Document metadata updated successfully"
    }


@router.delete("/my/documents/{document_id}")
async def delete_my_document(
    document_id: str,
    current_patient: PatientProfile = Depends(get_current_patient),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a document for the logged-in patient.
    
    Access Control:
    - **Who can access:** Patients only (documents they uploaded, from JWT token)
    """
    patient = current_patient
    
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    if document.uploaded_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - can only delete documents you uploaded"
        )
    
    if os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
        except OSError:
            pass
    
    await db.delete(document)
    await db.commit()
    
    return {
        "document_id": str(document.id),
        "message": "Document deleted successfully"
    }


# ============================================================================
# DOCUMENT UPLOAD ENDPOINTS (for doctors/staff - patient_ref required)
# ============================================================================

@router.post("/patients/{patient_ref}/documents/upload")
async def upload_patient_document(
    patient_ref: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    document_date: Optional[str] = Form(None),
    is_sensitive: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Upload a document for a patient (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (any patient in hospital), Patients (own only)
    - For patients: prefer POST /my/documents/upload (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Validate document type
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
        )
    
    # Validate file
    if not validate_file_type(file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Allowed types: PDF, Images (JPEG, PNG, GIF), Word documents, Excel files, Text files"
        )
    
    if not validate_file_size(file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10MB limit"
        )
    
    # Get patient - handle cases where user's hospital_id might be null
    # Try to get patient first, then use patient's hospital_id if available
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # If user's hospital_id is null but patient has hospital_id, use patient's hospital_id
    if not user_context.get("hospital_id") and patient.hospital_id:
        user_context["hospital_id"] = str(patient.hospital_id)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        if str(patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - you can only upload documents for your own profile"
            )
    elif user_context["role"] == UserRole.HOSPITAL_ADMIN:
        # Hospital admins can upload documents for any patient in their hospital
        pass
    elif user_context["role"] == UserRole.RECEPTIONIST:
        hid = user_context.get("hospital_id")
        if (
            hid
            and patient.hospital_id
            and str(patient.hospital_id) != str(hid)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - patient is not in your hospital",
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            # Check if doctor has medical records for this patient
            from app.models.patient import MedicalRecord
            record_check = await db.execute(
                select(MedicalRecord)
                .where(
                    and_(
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    
    # Generate unique filename
    file_extension = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    
    # Ensure we have a hospital_id - use patient's hospital_id if user's is null
    hospital_id_for_doc = user_context.get("hospital_id")
    if not hospital_id_for_doc and patient.hospital_id:
        hospital_id_for_doc = str(patient.hospital_id)
    
    if not hospital_id_for_doc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Please book an appointment first to link your account to a hospital."
        )
    
    # Get upload directory
    upload_dir = get_upload_directory(hospital_id_for_doc, patient_ref)
    file_path = os.path.join(upload_dir, unique_filename)
    
    try:
        # Save file
        file_size = await save_uploaded_file(file, file_path)
        
        # Create document record
        document = PatientDocument(
            id=uuid.uuid4(),
            hospital_id=uuid.UUID(hospital_id_for_doc),
            patient_id=patient.id,
            uploaded_by=user_context["user_id"],
            document_type=document_type,
            title=title,
            description=description,
            file_name=file.filename or unique_filename,
            file_path=file_path,
            file_size=file_size,
            mime_type=file.content_type,
            document_date=document_date,
            is_sensitive=is_sensitive
        )
        
        db.add(document)
        await db.commit()
        
        return DocumentUploadOut(
            document_id=str(document.id),
            patient_ref=patient_ref,
            document_type=document_type,
            title=title,
            file_name=file.filename or unique_filename,
            file_size=file_size,
            upload_date=document.created_at.isoformat(),
            message="Document uploaded successfully"
        )
        
    except Exception as e:
        # Clean up file if database operation fails
        if os.path.exists(file_path):
            os.remove(file_path)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload document: {str(e)}"
        )


# ============================================================================
# DOCUMENT MANAGEMENT ENDPOINTS
# ============================================================================

@router.get("/patients/{patient_ref}/documents")
async def get_patient_documents(
    patient_ref: str,
    document_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get paginated list of patient documents (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients: prefer GET /my/documents (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        if str(patient.user_id) != user_context["user_id"]:
            # Get the correct patient reference for this user
            correct_patient_result = await db.execute(
                select(PatientProfile)
                .where(PatientProfile.user_id == user_context["user_id"])
            )
            correct_patient = correct_patient_result.scalar_one_or_none()
            
            if correct_patient:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied - You can only view your own documents. Your patient reference is: {correct_patient.patient_id}"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - No patient profile found for your account"
                )
    elif user_context["role"] == UserRole.HOSPITAL_ADMIN:
        # Hospital admins can view all patient documents in their hospital
        # Patient is already filtered by hospital_id in get_patient_by_ref
        pass
    elif user_context["role"] == UserRole.RECEPTIONIST:
        hid = user_context.get("hospital_id")
        if (
            hid
            and patient.hospital_id
            and str(patient.hospital_id) != str(hid)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - patient is not in your hospital",
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        has_access = await check_doctor_patient_access(user_context["user_id"], patient.id, db)
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - no treatment history with this patient"
            )
    else:
        # Other roles don't have access
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - insufficient permissions"
        )
    
    # Build query
    offset = (page - 1) * limit
    
    query = select(PatientDocument).where(
        PatientDocument.patient_id == patient.id
    ).options(
        selectinload(PatientDocument.uploader)
    ).order_by(desc(PatientDocument.created_at))
    
    # Apply document type filter
    if document_type:
        try:
            DocumentType(document_type)  # Validate enum
            query = query.where(PatientDocument.document_type == document_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
            )
    
    # Get total count
    count_query = select(func.count(PatientDocument.id)).where(
        PatientDocument.patient_id == patient.id
    )
    if document_type:
        count_query = count_query.where(PatientDocument.document_type == document_type)
    
    total_result = await db.execute(count_query)
    total_documents = total_result.scalar() or 0
    
    # Get paginated documents
    documents_result = await db.execute(query.offset(offset).limit(limit))
    documents = documents_result.scalars().all()
    
    # Format response
    document_list = []
    for doc in documents:
        document_list.append(DocumentListOut(
            document_id=str(doc.id),
            document_type=doc.document_type,
            title=doc.title,
            description=doc.description,
            file_name=doc.file_name,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            document_date=doc.document_date,
            upload_date=doc.created_at.isoformat(),
            uploaded_by=f"{doc.uploader.first_name} {doc.uploader.last_name}",
            is_sensitive=doc.is_sensitive
        ))
    
    return {
        "patient_ref": patient_ref,
        "documents": document_list,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_documents,
            "pages": (total_documents + limit - 1) // limit
        }
    }


@router.get("/patients/{patient_ref}/documents/{document_id}")
async def get_document_details(
    patient_ref: str,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed information about a specific document (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients: prefer GET /my/documents/{document_id} (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Get document
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
        .options(selectinload(PatientDocument.uploader))
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Role-based access control
    if user_context["role"] == UserRole.PATIENT:
        if str(patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            # Check if doctor has medical records for this patient
            from app.models.patient import MedicalRecord
            record_check = await db.execute(
                select(MedicalRecord)
                .where(
                    and_(
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    elif user_context["role"] == UserRole.RECEPTIONIST:
        hid = user_context.get("hospital_id")
        if (
            hid
            and patient.hospital_id
            and str(patient.hospital_id) != str(hid)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - patient is not in your hospital",
            )
    
    return {
        "document_id": str(document.id),
        "patient_ref": patient_ref,
        "document_type": document.document_type,
        "title": document.title,
        "description": document.description,
        "file_name": document.file_name,
        "file_size": document.file_size,
        "mime_type": document.mime_type,
        "document_date": document.document_date,
        "is_sensitive": document.is_sensitive,
        "upload_date": document.created_at.isoformat(),
        "uploaded_by": f"{document.uploader.first_name} {document.uploader.last_name}",
        "uploader_role": [role.name for role in document.uploader.roles][0] if document.uploader.roles else "Unknown"
    }


@router.get("/patients/{patient_ref}/documents/{document_id}/download")
async def download_document(
    patient_ref: str,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Download a patient document (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients: prefer GET /my/documents/{document_id}/download (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Get document
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Role-based access control (same as get_document_details)
    if user_context["role"] == UserRole.PATIENT:
        if str(patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            # Check if doctor has medical records for this patient
            from app.models.patient import MedicalRecord
            record_check = await db.execute(
                select(MedicalRecord)
                .where(
                    and_(
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    elif user_context["role"] == UserRole.RECEPTIONIST:
        hid = user_context.get("hospital_id")
        if (
            hid
            and patient.hospital_id
            and str(patient.hospital_id) != str(hid)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - patient is not in your hospital",
            )
    
    # Check if file exists
    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk"
        )
    
    # Return file
    return FileResponse(
        path=document.file_path,
        filename=document.file_name,
        media_type=document.mime_type or 'application/octet-stream'
    )


@router.patch("/patients/{patient_ref}/documents/{document_id}")
async def update_document_metadata(
    patient_ref: str,
    document_id: str,
    update_data: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update document metadata (title, description, etc.).
    
    Access Control:
    - **Who can access:** Hospital Admins (any document in hospital), Document uploader (own uploads only)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Get document
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Role-based access control
    if user_context["role"] not in [UserRole.HOSPITAL_ADMIN]:
        if document.uploaded_by != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - can only update documents you uploaded"
            )
    
    # Validate document type if provided
    if update_data.document_type:
        try:
            DocumentType(update_data.document_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid document type. Allowed types: {[dt.value for dt in DocumentType]}"
            )
    
    # Update fields
    update_fields = update_data.dict(exclude_unset=True)
    
    for field, value in update_fields.items():
        setattr(document, field, value)
    
    await db.commit()
    
    return {
        "document_id": str(document.id),
        "message": "Document metadata updated successfully"
    }


@router.delete("/patients/{patient_ref}/documents/{document_id}")
async def delete_document(
    patient_ref: str,
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a patient document.
    
    Access Control:
    - **Who can access:** Hospital Admins (any document in hospital), Document uploader (own uploads only)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Get document
    document_result = await db.execute(
        select(PatientDocument)
        .where(
            and_(
                PatientDocument.id == document_id,
                PatientDocument.patient_id == patient.id
            )
        )
    )
    
    document = document_result.scalar_one_or_none()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Role-based access control
    if user_context["role"] not in [UserRole.HOSPITAL_ADMIN]:
        if document.uploaded_by != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - can only delete documents you uploaded"
            )
    
    # Delete file from disk
    if os.path.exists(document.file_path):
        try:
            os.remove(document.file_path)
        except OSError:
            pass  # Continue even if file deletion fails
    
    # Delete database record
    await db.delete(document)
    await db.commit()
    
    return {
        "document_id": str(document.id),
        "message": "Document deleted successfully"
    }


# ============================================================================
# DOCUMENT STATISTICS ENDPOINTS
# ============================================================================

@router.get("/patients/{patient_ref}/documents/statistics")
async def get_document_statistics(
    patient_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get document statistics for a patient (staff view - requires patient_ref).
    
    Access Control:
    - **Who can access:** Doctors (patients they've treated), Hospital Admins (all in hospital), Patients (own only)
    - For patients: prefer GET /my/documents/statistics (no patient_ref needed)
    """
    user_context = get_user_context(current_user)
    
    # Get patient
    patient = await get_patient_by_ref(patient_ref, user_context.get("hospital_id"), db)
    
    # Role-based access control (same as other endpoints)
    if user_context["role"] == UserRole.PATIENT:
        if str(patient.user_id) != user_context["user_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
    elif user_context["role"] == UserRole.DOCTOR:
        # Check if doctor has treated this patient
        doctor_result = await db.execute(
            select(DoctorProfile)
            .where(DoctorProfile.user_id == user_context["user_id"])
        )
        doctor = doctor_result.scalar_one_or_none()
        
        if doctor:
            # Check if doctor has medical records for this patient
            from app.models.patient import MedicalRecord
            record_check = await db.execute(
                select(MedicalRecord)
                .where(
                    and_(
                        MedicalRecord.patient_id == patient.id,
                        MedicalRecord.doctor_id == doctor.id
                    )
                )
                .limit(1)
            )
            
            if not record_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied - no treatment history"
                )
    elif user_context["role"] == UserRole.RECEPTIONIST:
        hid = user_context.get("hospital_id")
        if (
            hid
            and patient.hospital_id
            and str(patient.hospital_id) != str(hid)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - patient is not in your hospital",
            )
    
    # Get document statistics
    stats_result = await db.execute(
        select(
            PatientDocument.document_type,
            func.count(PatientDocument.id).label('count'),
            func.sum(PatientDocument.file_size).label('total_size')
        )
        .where(PatientDocument.patient_id == patient.id)
        .group_by(PatientDocument.document_type)
    )
    
    stats = stats_result.all()
    
    # Get total statistics
    total_result = await db.execute(
        select(
            func.count(PatientDocument.id).label('total_documents'),
            func.sum(PatientDocument.file_size).label('total_size')
        )
        .where(PatientDocument.patient_id == patient.id)
    )
    
    total_stats = total_result.first()
    
    # Format response
    document_types = {}
    for stat in stats:
        document_types[stat.document_type] = {
            "count": stat.count,
            "total_size": stat.total_size or 0
        }
    
    return {
        "patient_ref": patient_ref,
        "total_documents": total_stats.total_documents or 0,
        "total_size": total_stats.total_size or 0,
        "document_types": document_types
    }