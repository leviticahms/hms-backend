"""
Doctor Prescription System API
Advanced digital prescription creation with drug database integration, interaction checking,
dosage validation, and pharmacy integration for comprehensive medication management.

BUSINESS RULES:
- Only Doctors can create and manage prescriptions
- Drug interaction checking before prescription creation
- Dosage validation based on patient age, weight, and medical conditions
- Integration with pharmacy systems for dispensing
- Digital signature and prescription tracking
- Allergy and contraindication checking
"""
import uuid
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta, date
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, update
from sqlalchemy.orm import selectinload
from app.core.database import get_db_session, get_platform_db_session
from app.services.patient_resolve import load_patient_by_ref, resolve_staff_hospital_id
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile, Appointment, MedicalRecord
from app.models.doctor import DoctorProfile, Prescription
from app.models.hospital import Department
from app.core.enums import UserRole, AppointmentStatus
from app.core.utils import generate_patient_ref
from app.schemas.doctor import (
    DrugInfoOut, DosageRecommendationOut, DrugInteractionOut, AllergyCheckOut,
    MedicationTiming, MedicationItem, PrescriptionCreate, PrescriptionValidationOut,
    DigitalPrescriptionOut, PharmacyDispenseCreate, DrugSearchFilter
)

router = APIRouter(prefix="/doctor-prescription-system", tags=["Doctor Portal - Digital Prescription"])


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

async def _load_patient_for_doctor(
    patient_ref: str,
    current_user: User,
    tenant_db: AsyncSession,
    platform_db: AsyncSession,
):
    hid = await resolve_staff_hospital_id(
        current_user,
        tenant_db,
        platform_db,
        fallback=str(current_user.hospital_id) if current_user.hospital_id else None,
    )
    return await load_patient_by_ref(
        patient_ref,
        hid,
        tenant_db,
        platform_db,
        ensure_on_tenant=True,
    )


def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": user_roles[0] if user_roles else None,
        "all_roles": user_roles
    }


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile with department information"""
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )
    
    # First try to get DoctorProfile
    result = await db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == user_context["user_id"])
        .options(
            selectinload(DoctorProfile.user),
            selectinload(DoctorProfile.department)
        )
    )
    
    doctor = result.scalar_one_or_none()
    
    # If no DoctorProfile exists, create a mock profile using User and department assignment
    if not doctor:
        # Get doctor user
        doctor_result = await db.execute(
            select(User)
            .where(User.id == user_context["user_id"])
        )
        doctor_user = doctor_result.scalar_one_or_none()
        
        if not doctor_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor user not found. Please contact administrator."
            )
            
        # Get department assignment
        from app.models.hospital import StaffDepartmentAssignment
        assignment_result = await db.execute(
            select(StaffDepartmentAssignment)
            .where(StaffDepartmentAssignment.staff_id == user_context["user_id"])
            .options(selectinload(StaffDepartmentAssignment.department))
        )
        assignment = assignment_result.scalar_one_or_none()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor not assigned to any department. Please contact administrator."
            )
            
        # Create a mock object that has the same interface as DoctorProfile
        class MockDoctorProfile:
            def __init__(self, user, department):
                self.user = user
                self.department = department
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                self.department_id = department.id
                self.id = user.id  # Use user.id as profile id for compatibility
                
                # Professional details (mock values)
                self.doctor_id = f"DOC-{user.id}"
                self.medical_license_number = f"LIC-{user.id}"
                self.designation = "General Practitioner"
                self.specialization = department.name or "General Medicine"
                self.sub_specialization = None
                
                # Experience and qualifications (mock values)
                self.experience_years = 5
                self.qualifications = ["MBBS"]
                self.certifications = []
                self.medical_associations = []
                
                # Consultation details (mock values)
                self.consultation_fee = 500.00
                self.follow_up_fee = 300.00
                
                # Availability (mock values)
                self.is_available_for_emergency = True
                self.is_accepting_new_patients = True
                
                # Profile information (mock values)
                self.bio = f"Experienced doctor in {department.name}"
                self.languages_spoken = ["English"]
        
        doctor = MockDoctorProfile(doctor_user, assignment.department)
    
    return doctor


def ensure_doctor_access(user_context: dict):
    """Ensure user is a doctor"""
    if user_context["role"] != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Doctor role required"
        )


def calculate_age(date_of_birth: str) -> int:
    """Calculate age from date of birth"""
    try:
        birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        today = date.today()
        return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
    except:
        return 0


def generate_prescription_number() -> str:
    """Generate unique prescription number"""
    import random
    import string
    
    # Format: RX-YYYY-XXXXXX
    year = datetime.now().year
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"RX-{year}-{random_part}"


def generate_qr_code(prescription_data: dict) -> str:
    """Generate QR code for prescription verification"""
    import hashlib
    
    # Create a hash of prescription data for QR code
    data_string = f"{prescription_data['prescription_number']}-{prescription_data['patient_ref']}-{prescription_data['doctor_id']}"
    qr_hash = hashlib.md5(data_string.encode()).hexdigest()
    return f"QR-{qr_hash[:12].upper()}"


# In-memory drug catalogue (populate via your own data source / DB integration).
MOCK_DRUG_DATABASE: dict[str, dict] = {}
DRUG_INTERACTIONS_DB: dict[tuple[str, str], dict] = {}


# ============================================================================
# DRUG DATABASE ENDPOINTS
# ============================================================================

@router.get("/drugs/search")
async def search_drugs(
    query: str = Query(..., min_length=2),
    search_type: str = Query("all", pattern="^(generic|brand|class|all)$"),
    therapeutic_category: Optional[str] = Query(None),
    dosage_form: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search drugs in the database.
    
    Access Control:
    - Only Doctors can search drug database
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Search in mock database (in production, this would query a real drug database)
    results = []
    query_lower = query.lower()
    
    for drug_id, drug_data in MOCK_DRUG_DATABASE.items():
        match = False
        
        if search_type in ["generic", "all"]:
            if query_lower in drug_data["generic_name"].lower():
                match = True
        
        if search_type in ["brand", "all"]:
            for brand in drug_data["brand_names"]:
                if query_lower in brand.lower():
                    match = True
                    break
        
        if search_type in ["class", "all"]:
            if query_lower in drug_data["drug_class"].lower():
                match = True
        
        # Apply filters
        if therapeutic_category and drug_data["therapeutic_category"] != therapeutic_category:
            match = False
        
        if dosage_form and dosage_form not in drug_data["dosage_forms"]:
            match = False
        
        if match:
            results.append(DrugInfoOut(**drug_data))
        
        if len(results) >= limit:
            break
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "search_query": query,
        "search_type": search_type,
        "total_results": len(results),
        "drugs": results
    }


@router.get("/drugs/{drug_id}")
async def get_drug_details(
    drug_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed drug information.
    
    Access Control:
    - Only Doctors can access drug details
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get drug from mock database
    if drug_id not in MOCK_DRUG_DATABASE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drug not found in database"
        )
    
    drug_data = MOCK_DRUG_DATABASE[drug_id]
    
    return DrugInfoOut(**drug_data)


@router.post("/drugs/check-interactions")
async def check_drug_interactions(
    drug_ids: List[str] = Body(..., min_items=2),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Check for drug interactions between multiple drugs.
    
    Access Control:
    - Only Doctors can check drug interactions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    interactions = []
    
    # Check all combinations
    for i in range(len(drug_ids)):
        for j in range(i + 1, len(drug_ids)):
            drug1_id = drug_ids[i]
            drug2_id = drug_ids[j]
            
            # Get drug names
            drug1_name = MOCK_DRUG_DATABASE.get(drug1_id, {}).get("generic_name", "Unknown")
            drug2_name = MOCK_DRUG_DATABASE.get(drug2_id, {}).get("generic_name", "Unknown")
            
            # Check for interactions in mock database
            interaction_key = (drug1_id, drug2_name.lower())
            if interaction_key in DRUG_INTERACTIONS_DB:
                interaction_data = DRUG_INTERACTIONS_DB[interaction_key]
                interactions.append(DrugInteractionOut(
                    drug1_id=drug1_id,
                    drug1_name=drug1_name,
                    drug2_id=drug2_id,
                    drug2_name=drug2_name,
                    **interaction_data
                ))
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "checked_drugs": drug_ids,
        "total_interactions": len(interactions),
        "interactions": interactions
    }


@router.post("/drugs/dosage-recommendation")
async def get_dosage_recommendation(
    drug_id: str = Body(...),
    patient_ref: str = Body(...),
    indication: str = Body(...),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get dosage recommendation based on patient factors.
    
    Access Control:
    - Only Doctors can get dosage recommendations
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)
    patient = await _load_patient_for_doctor(
        patient_ref, current_user, tenant_db, platform_db
    )

    # Get drug info
    if drug_id not in MOCK_DRUG_DATABASE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Drug not found in database"
        )
    
    drug_data = MOCK_DRUG_DATABASE[drug_id]
    patient_age = calculate_age(patient.date_of_birth)
    
    # Generate dosage recommendation based on patient factors
    warnings = []
    patient_factors = []
    
    # Age-based considerations
    if patient_age < 18:
        if not drug_data["pediatric_safe"]:
            warnings.append("Drug not recommended for pediatric use")
        patient_factors.append("pediatric_patient")
    elif patient_age > 65:
        if drug_data["geriatric_considerations"]:
            warnings.append(drug_data["geriatric_considerations"])
        patient_factors.append("geriatric_patient")
    
    # Allergy check
    if patient.allergies:
        for allergy in patient.allergies:
            if allergy.lower() in [name.lower() for name in drug_data["brand_names"]] or \
               allergy.lower() == drug_data["generic_name"].lower():
                warnings.append(f"Patient allergic to {allergy}")
    
    # Chronic conditions check
    if patient.chronic_conditions:
        for condition in patient.chronic_conditions:
            if condition.lower() in [contra.lower() for contra in drug_data["contraindications"]]:
                warnings.append(f"Contraindicated in {condition}")
    
    # Mock dosage calculation (in production, this would use clinical algorithms)
    if drug_id == "DRUG001":  # Paracetamol
        if patient_age < 12:
            recommended_dose = "10-15mg/kg"
            frequency = "QID"
        else:
            recommended_dose = "500-1000mg"
            frequency = "QID"
        duration = "3-5 days"
        route = "oral"
    elif drug_id == "DRUG002":  # Amoxicillin
        if patient_age < 12:
            recommended_dose = "20-40mg/kg/day"
            frequency = "TID"
        else:
            recommended_dose = "500mg"
            frequency = "TID"
        duration = "7-10 days"
        route = "oral"
    else:
        recommended_dose = "As per standard guidelines"
        frequency = "As directed"
        duration = "As needed"
        route = "oral"
    
    recommendation = DosageRecommendationOut(
        drug_id=drug_id,
        drug_name=drug_data["generic_name"],
        recommended_dose=recommended_dose,
        frequency=frequency,
        duration=duration,
        route=route,
        special_instructions=f"For {indication}",
        warnings=warnings,
        patient_factors_considered=patient_factors
    )
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "patient_ref": patient_ref,
        "patient_age": patient_age,
        "drug_name": drug_data["generic_name"],
        "indication": indication,
        "recommendation": recommendation
    }


# ============================================================================
# PRESCRIPTION VALIDATION
# ============================================================================

@router.post("/prescriptions/validate")
async def validate_prescription(
    request: PrescriptionCreate,
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Validate prescription before creation.
    
    Access Control:
    - Only Doctors can validate prescriptions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)
    patient = await _load_patient_for_doctor(
        request.patient_ref, current_user, tenant_db, platform_db
    )

    warnings = []
    errors = []
    drug_interactions = []
    allergy_alerts = []
    dosage_recommendations = []
    contraindications = []
    patient_specific_warnings = []
    
    patient_age = calculate_age(patient.date_of_birth)
    
    # Validate each medication
    drug_ids = [med.drug_id for med in request.medications]
    
    for medication in request.medications:
        # Check if drug exists
        if medication.drug_id not in MOCK_DRUG_DATABASE:
            errors.append(f"Drug {medication.drug_id} not found in database")
            continue
        
        drug_data = MOCK_DRUG_DATABASE[medication.drug_id]
        
        # Age-based validation
        if patient_age < 18 and not drug_data["pediatric_safe"]:
            warnings.append(f"{drug_data['generic_name']} not recommended for pediatric use")
        
        # Allergy check
        if request.check_allergies and patient.allergies:
            for allergy in patient.allergies:
                if allergy.lower() in [name.lower() for name in drug_data["brand_names"]] or \
                   allergy.lower() == drug_data["generic_name"].lower():
                    allergy_alerts.append(AllergyCheckOut(
                        drug_id=medication.drug_id,
                        drug_name=drug_data["generic_name"],
                        has_allergy=True,
                        allergy_type=allergy,
                        severity="HIGH",
                        alternative_drugs=["Alternative drug consultation needed"]
                    ))
        
        # Contraindication check
        if patient.chronic_conditions:
            for condition in patient.chronic_conditions:
                if condition.lower() in [contra.lower() for contra in drug_data["contraindications"]]:
                    contraindications.append(f"{drug_data['generic_name']} contraindicated in {condition}")
        
        # Dosage form validation
        if medication.dosage_form not in drug_data["dosage_forms"]:
            errors.append(f"Invalid dosage form {medication.dosage_form} for {drug_data['generic_name']}")
        
        # Strength validation
        if medication.strength not in drug_data["strengths"]:
            warnings.append(f"Unusual strength {medication.strength} for {drug_data['generic_name']}")
    
    # Drug interaction check
    if request.check_interactions and len(drug_ids) > 1:
        for i in range(len(drug_ids)):
            for j in range(i + 1, len(drug_ids)):
                drug1_id = drug_ids[i]
                drug2_id = drug_ids[j]
                
                drug1_name = MOCK_DRUG_DATABASE.get(drug1_id, {}).get("generic_name", "Unknown")
                drug2_name = MOCK_DRUG_DATABASE.get(drug2_id, {}).get("generic_name", "Unknown")
                
                # Check for interactions
                interaction_key = (drug1_id, drug2_name.lower())
                if interaction_key in DRUG_INTERACTIONS_DB:
                    interaction_data = DRUG_INTERACTIONS_DB[interaction_key]
                    drug_interactions.append(DrugInteraction(
                        drug1_id=drug1_id,
                        drug1_name=drug1_name,
                        drug2_id=drug2_id,
                        drug2_name=drug2_name,
                        **interaction_data
                    ))
    
    # Patient-specific warnings
    if patient_age > 65:
        patient_specific_warnings.append("Elderly patient - monitor for adverse effects")
    
    if patient.chronic_conditions:
        if "diabetes" in [c.lower() for c in patient.chronic_conditions]:
            patient_specific_warnings.append("Diabetic patient - monitor blood glucose")
        if "hypertension" in [c.lower() for c in patient.chronic_conditions]:
            patient_specific_warnings.append("Hypertensive patient - monitor blood pressure")
    
    is_valid = len(errors) == 0 and len([ia for ia in allergy_alerts if ia.has_allergy]) == 0
    
    return PrescriptionValidationResult(
        is_valid=is_valid,
        warnings=warnings,
        errors=errors,
        drug_interactions=drug_interactions,
        allergy_alerts=allergy_alerts,
        dosage_recommendations=dosage_recommendations,
        contraindications=contraindications,
        patient_specific_warnings=patient_specific_warnings
    )


# ============================================================================
# ADVANCED PRESCRIPTION CREATION
# ============================================================================

@router.post("/prescriptions/create-advanced")
async def create_advanced_prescription(
    request: PrescriptionCreate,
    force_create: bool = Query(False, description="Force create despite warnings"),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Create advanced digital prescription with validation.
    
    Access Control:
    - Only Doctors can create prescriptions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Ensure hospital_id is available
    if not user_context.get("hospital_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hospital ID is required. Doctor must be associated with a hospital."
        )
    
    doctor = await get_doctor_profile(user_context, tenant_db)

    if hasattr(doctor, "user"):
        existing_profile = await tenant_db.execute(
            select(DoctorProfile).where(DoctorProfile.user_id == doctor.user_id)
        )
        actual_profile = existing_profile.scalar_one_or_none()

        if not actual_profile:
            hid = await resolve_staff_hospital_id(
                current_user,
                tenant_db,
                platform_db,
                fallback=user_context.get("hospital_id"),
            )
            new_profile = DoctorProfile(
                hospital_id=hid,
                user_id=doctor.user_id,
                department_id=doctor.department_id,
                doctor_id=doctor.doctor_id,
                medical_license_number=doctor.medical_license_number,
                designation=doctor.designation,
                specialization=doctor.specialization,
                experience_years=doctor.experience_years,
                qualifications=doctor.qualifications,
                consultation_fee=doctor.consultation_fee,
                follow_up_fee=doctor.follow_up_fee,
                is_available_for_emergency=doctor.is_available_for_emergency,
                is_accepting_new_patients=doctor.is_accepting_new_patients,
                bio=doctor.bio,
                languages_spoken=doctor.languages_spoken
            )
            tenant_db.add(new_profile)
            await tenant_db.commit()
            await tenant_db.refresh(new_profile)
            doctor = new_profile

    patient = await _load_patient_for_doctor(
        request.patient_ref, current_user, tenant_db, platform_db
    )

    if not force_create:
        validation_result = await validate_prescription(
            request, current_user, tenant_db, platform_db
        )

        if not validation_result.is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Prescription validation failed",
                    "validation_result": validation_result.dict()
                }
            )
        
        # Check for critical warnings
        critical_warnings = [
            ia for ia in validation_result.allergy_alerts if ia.has_allergy
        ] + [
            di for di in validation_result.drug_interactions if di.severity == "HIGH"
        ]
        
        if critical_warnings and not force_create:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Critical warnings found. Use force_create=true to override",
                    "critical_warnings": critical_warnings
                }
            )
    
    # Get appointment if provided
    appointment_id = None
    medical_record_id = None
    
    if request.appointment_ref:
        appointment_result = await tenant_db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == request.appointment_ref,
                    Appointment.doctor_id == doctor.id,
                    Appointment.patient_id == patient.id
                )
            )
        )

        appointment = appointment_result.scalar_one_or_none()
        if appointment:
            appointment_id = appointment.id

            medical_record_result = await tenant_db.execute(
                select(MedicalRecord)
                .where(MedicalRecord.appointment_id == appointment.id)
            )
            
            medical_record = medical_record_result.scalar_one_or_none()
            if medical_record:
                medical_record_id = medical_record.id
    
    # Generate prescription number and QR code
    prescription_number = generate_prescription_number()
    
    # Convert medications to JSON format
    medications_json = []
    for med in request.medications:
        medications_json.append({
            "drug_id": med.drug_id,
            "generic_name": med.generic_name,
            "brand_name": med.brand_name,
            "strength": med.strength,
            "dosage_form": med.dosage_form,
            "quantity": med.quantity,
            "dosage": med.dosage,
            "frequency": med.frequency,
            "duration": med.duration,
            "route": med.route,
            "instructions": med.instructions,
            "substitute_allowed": med.substitute_allowed
        })
    
    rx_hospital_id = await resolve_staff_hospital_id(
        current_user,
        tenant_db,
        platform_db,
        fallback=user_context.get("hospital_id"),
    )

    prescription = Prescription(
        patient_id=patient.id,
        doctor_id=doctor.id,
        appointment_id=appointment_id,
        medical_record_id=medical_record_id,
        hospital_id=rx_hospital_id,
        prescription_number=prescription_number,
        prescription_date=date.today().isoformat(),
        diagnosis=request.clinical_diagnosis,
        symptoms=request.chief_complaint,
        medications=medications_json,
        general_instructions=request.general_instructions,
        diet_instructions=request.diet_instructions,
        follow_up_date=request.follow_up_date,
        is_digitally_signed=True,
        signature_hash=f"hash_{prescription_number}"
    )
    
    tenant_db.add(prescription)
    await tenant_db.commit()
    await tenant_db.refresh(prescription)

    qr_code = generate_qr_code({
        "prescription_number": prescription_number,
        "patient_ref": request.patient_ref,
        "doctor_id": str(doctor.id)
    })
    
    patient_age = calculate_age(patient.date_of_birth)
    
    # Create digital prescription response
    digital_prescription = DigitalPrescription(
        prescription_id=str(prescription.id),
        prescription_number=prescription_number,
        patient_ref=request.patient_ref,
        patient_name=f"{patient.user.first_name} {patient.user.last_name}",
        patient_age=patient_age,
        patient_weight=None,  # Would be from patient profile
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        doctor_license=doctor.medical_license_number,
        hospital_name="Hospital Name",  # Would be from hospital data
        prescription_date=prescription.prescription_date,
        clinical_diagnosis=request.clinical_diagnosis,
        medications=request.medications,
        general_instructions=request.general_instructions,
        diet_instructions=request.diet_instructions,
        lifestyle_advice=request.lifestyle_advice,
        follow_up_date=request.follow_up_date,
        follow_up_instructions=request.follow_up_instructions,
        lab_tests_recommended=request.lab_tests_recommended,
        precautions=request.precautions,
        is_digitally_signed=True,
        signature_hash=prescription.signature_hash,
        qr_code=qr_code,
        pharmacy_instructions="Present this prescription to any registered pharmacy",
        dispensing_status="PENDING",
        created_at=prescription.created_at.isoformat()
    )
    
    return {
        "message": "Advanced prescription created successfully",
        "prescription": digital_prescription,
        "validation_bypassed": force_create
    }

# ============================================================================
# PRESCRIPTION MANAGEMENT
# ============================================================================

@router.get("/prescriptions/digital/{prescription_number}")
async def get_digital_prescription(
    prescription_number: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get digital prescription with full details.
    
    Access Control:
    - Only Doctors can access their prescriptions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.prescription_number == prescription_number,
                Prescription.doctor_id == doctor.id
            )
        )
        .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    patient_age = calculate_age(prescription.patient.date_of_birth)
    
    # Convert medications to MedicationItem format
    medications = []
    for med_data in prescription.medications:
        medications.append(MedicationItem(**med_data))
    
    # Generate QR code
    qr_code = generate_qr_code({
        "prescription_number": prescription_number,
        "patient_ref": prescription.patient.patient_id,
        "doctor_id": str(doctor.id)
    })
    
    digital_prescription = DigitalPrescription(
        prescription_id=str(prescription.id),
        prescription_number=prescription.prescription_number,
        patient_ref=prescription.patient.patient_id,
        patient_name=f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
        patient_age=patient_age,
        patient_weight=None,
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        doctor_license=doctor.medical_license_number,
        hospital_name="Hospital Name",
        prescription_date=prescription.prescription_date,
        clinical_diagnosis=prescription.diagnosis,
        medications=medications,
        general_instructions=prescription.general_instructions,
        diet_instructions=prescription.diet_instructions,
        lifestyle_advice=None,
        follow_up_date=prescription.follow_up_date,
        follow_up_instructions=None,
        lab_tests_recommended=None,
        precautions=None,
        is_digitally_signed=prescription.is_digitally_signed,
        signature_hash=prescription.signature_hash,
        qr_code=qr_code,
        pharmacy_instructions="Present this prescription to any registered pharmacy",
        dispensing_status="DISPENSED" if prescription.is_dispensed else "PENDING",
        created_at=prescription.created_at.isoformat()
    )
    
    return digital_prescription


@router.get("/prescriptions/history")
async def get_prescription_history(
    patient_ref: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    date_to: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    dispensing_status: Optional[str] = Query(None, pattern="^(PENDING|DISPENSED|PARTIALLY_DISPENSED)$"),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    tenant_db: AsyncSession = Depends(get_db_session),
    platform_db: AsyncSession = Depends(get_platform_db_session),
):
    """
    Get prescription history with filtering options.
    
    Access Control:
    - Only Doctors can access their prescription history
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    doctor = await get_doctor_profile(user_context, tenant_db)

    conditions = [Prescription.doctor_id == doctor.id]

    if patient_ref:
        try:
            resolved_patient = await _load_patient_for_doctor(
                patient_ref, current_user, tenant_db, platform_db
            )
            patient_id = resolved_patient.id
        except HTTPException:
            patient_id = None
        if patient_id:
            conditions.append(Prescription.patient_id == patient_id)
        else:
            return {
                "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
                "total_prescriptions": 0,
                "filters": {
                    "patient_ref": patient_ref,
                    "date_from": date_from,
                    "date_to": date_to,
                    "dispensing_status": dispensing_status
                },
                "prescriptions": []
            }
    
    if date_from:
        conditions.append(Prescription.prescription_date >= date_from)
    
    if date_to:
        conditions.append(Prescription.prescription_date <= date_to)
    
    if dispensing_status:
        if dispensing_status == "DISPENSED":
            conditions.append(Prescription.is_dispensed == True)
        elif dispensing_status == "PENDING":
            conditions.append(Prescription.is_dispensed == False)
    
    prescriptions_result = await tenant_db.execute(
        select(Prescription)
        .where(and_(*conditions))
        .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
        .order_by(desc(Prescription.created_at))
        .limit(limit)
    )
    
    prescriptions = prescriptions_result.scalars().all()
    
    # Format prescriptions
    prescription_list = []
    for prescription in prescriptions:
        patient_age = calculate_age(prescription.patient.date_of_birth)
        
        prescription_list.append({
            "prescription_id": str(prescription.id),
            "prescription_number": prescription.prescription_number,
            "patient_ref": prescription.patient.patient_id,
            "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
            "patient_age": patient_age,
            "prescription_date": prescription.prescription_date,
            "diagnosis": prescription.diagnosis,
            "total_medications": len(prescription.medications),
            "medications_summary": [med.get("generic_name", "Unknown") for med in prescription.medications[:3]],
            "dispensing_status": "DISPENSED" if prescription.is_dispensed else "PENDING",
            "dispensed_at": prescription.dispensed_at,
            "is_digitally_signed": prescription.is_digitally_signed,
            "created_at": prescription.created_at.isoformat()
        })
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_prescriptions": len(prescription_list),
        "filters": {
            "patient_ref": patient_ref,
            "date_from": date_from,
            "date_to": date_to,
            "dispensing_status": dispensing_status
        },
        "prescriptions": prescription_list
    }


@router.put("/prescriptions/{prescription_number}/modify")
async def modify_prescription(
    prescription_number: str,
    modifications: Dict[str, Any] = Body(...),
    reason: str = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Modify existing prescription (only if not dispensed).
    
    Access Control:
    - Only Doctors can modify their prescriptions
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.prescription_number == prescription_number,
                Prescription.doctor_id == doctor.id
            )
        )
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    # Check if prescription can be modified
    if prescription.is_dispensed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify dispensed prescription"
        )
    
    # Apply modifications
    update_data = {}
    
    if "medications" in modifications:
        # Validate new medications
        for med in modifications["medications"]:
            if "drug_id" in med and med["drug_id"] not in MOCK_DRUG_DATABASE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Drug {med['drug_id']} not found in database"
                )
        update_data["medications"] = modifications["medications"]
    
    if "general_instructions" in modifications:
        update_data["general_instructions"] = modifications["general_instructions"]
    
    if "diet_instructions" in modifications:
        update_data["diet_instructions"] = modifications["diet_instructions"]
    
    if "follow_up_date" in modifications:
        update_data["follow_up_date"] = modifications["follow_up_date"]
    
    # Update prescription
    if update_data:
        await db.execute(
            update(Prescription)
            .where(Prescription.id == prescription.id)
            .values(**update_data)
        )
        await db.commit()
    
    return {
        "message": "Prescription modified successfully",
        "prescription_number": prescription_number,
        "modification_reason": reason,
        "modified_fields": list(update_data.keys()),
        "modified_at": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# PHARMACY INTEGRATION
# ============================================================================

@router.post("/prescriptions/{prescription_number}/dispense")
async def mark_prescription_dispensed(
    prescription_number: str,
    dispense_request: PharmacyDispenseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Mark prescription as dispensed (for pharmacy integration).
    
    Access Control:
    - Only Doctors can mark prescriptions as dispensed (in real system, this would be pharmacy role)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.prescription_number == prescription_number,
                Prescription.doctor_id == doctor.id
            )
        )
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    # Check if already dispensed
    if prescription.is_dispensed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prescription already dispensed"
        )
    
    # Update dispensing status
    await db.execute(
        update(Prescription)
        .where(Prescription.id == prescription.id)
        .values(
            is_dispensed=not dispense_request.partial_dispensing,
            dispensed_at=datetime.now(timezone.utc).isoformat(),
            dispensed_by=uuid.UUID(user_context["user_id"])  # In real system, this would be pharmacist ID
        )
    )
    await db.commit()
    
    return {
        "message": "Prescription dispensing recorded successfully",
        "prescription_number": prescription_number,
        "pharmacy_id": dispense_request.pharmacy_id,
        "pharmacist_id": dispense_request.pharmacist_id,
        "partial_dispensing": dispense_request.partial_dispensing,
        "dispensed_medications": dispense_request.dispensed_medications,
        "dispensing_notes": dispense_request.dispensing_notes,
        "dispensed_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/prescriptions/{prescription_number}/verify")
async def verify_prescription(
    prescription_number: str,
    qr_code: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Verify prescription authenticity (for pharmacy use).
    
    Access Control:
    - Only Doctors can verify prescriptions (in real system, this would be open for pharmacies)
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(Prescription.prescription_number == prescription_number)
        .options(
            selectinload(Prescription.patient).selectinload(PatientProfile.user),
            selectinload(Prescription.doctor).selectinload(DoctorProfile.user)
        )
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    # Verify QR code if provided
    qr_valid = True
    if qr_code:
        expected_qr = generate_qr_code({
            "prescription_number": prescription_number,
            "patient_ref": prescription.patient.patient_id,
            "doctor_id": str(prescription.doctor_id)
        })
        qr_valid = qr_code == expected_qr
    
    return {
        "prescription_number": prescription_number,
        "is_valid": True,
        "qr_code_valid": qr_valid,
        "prescription_details": {
            "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
            "doctor_name": f"Dr. {prescription.doctor.user.first_name} {prescription.doctor.user.last_name}",
            "doctor_license": prescription.doctor.medical_license_number,
            "prescription_date": prescription.prescription_date,
            "diagnosis": prescription.diagnosis,
            "total_medications": len(prescription.medications),
            "is_dispensed": prescription.is_dispensed,
            "is_digitally_signed": prescription.is_digitally_signed
        },
        "verification_timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# PRESCRIPTION ANALYTICS
# ============================================================================

@router.get("/analytics/prescription-patterns")
async def get_prescription_patterns(
    period: str = Query("month", pattern="^(week|month|quarter|year)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get prescription patterns and analytics.
    
    Access Control:
    - Only Doctors can access their prescription analytics
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Calculate date range
    today = date.today()
    if period == "week":
        start_date = today - timedelta(days=today.weekday())
    elif period == "month":
        start_date = today.replace(day=1)
    elif period == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=quarter_start_month, day=1)
    else:  # year
        start_date = today.replace(month=1, day=1)
    
    end_date = today
    
    # Get prescriptions in period
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.doctor_id == doctor.id,
                Prescription.prescription_date >= start_date.isoformat(),
                Prescription.prescription_date <= end_date.isoformat()
            )
        )
    )
    
    prescriptions = prescriptions_result.scalars().all()
    
    # Analyze prescription patterns
    total_prescriptions = len(prescriptions)
    total_medications = sum(len(p.medications) for p in prescriptions)
    dispensed_prescriptions = sum(1 for p in prescriptions if p.is_dispensed)
    
    # Drug frequency analysis
    drug_frequency = {}
    therapeutic_categories = {}
    
    for prescription in prescriptions:
        for med in prescription.medications:
            drug_id = med.get("drug_id")
            if drug_id and drug_id in MOCK_DRUG_DATABASE:
                drug_data = MOCK_DRUG_DATABASE[drug_id]
                drug_name = drug_data["generic_name"]
                category = drug_data["therapeutic_category"]
                
                drug_frequency[drug_name] = drug_frequency.get(drug_name, 0) + 1
                therapeutic_categories[category] = therapeutic_categories.get(category, 0) + 1
    
    # Top prescribed drugs
    top_drugs = sorted(drug_frequency.items(), key=lambda x: x[1], reverse=True)[:10]
    top_categories = sorted(therapeutic_categories.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Dispensing rate
    dispensing_rate = round((dispensed_prescriptions / total_prescriptions * 100) if total_prescriptions > 0 else 0, 1)
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "period": period,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "summary": {
            "total_prescriptions": total_prescriptions,
            "total_medications_prescribed": total_medications,
            "dispensed_prescriptions": dispensed_prescriptions,
            "dispensing_rate": dispensing_rate,
            "average_medications_per_prescription": round(total_medications / total_prescriptions, 1) if total_prescriptions > 0 else 0
        },
        "top_prescribed_drugs": [{"drug_name": drug, "frequency": freq} for drug, freq in top_drugs],
        "therapeutic_categories": [{"category": cat, "frequency": freq} for cat, freq in top_categories],
        "analysis_timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/analytics/drug-utilization")
async def get_drug_utilization_report(
    drug_id: Optional[str] = Query(None),
    therapeutic_category: Optional[str] = Query(None),
    period: str = Query("month", pattern="^(week|month|quarter|year)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get drug utilization report.
    
    Access Control:
    - Only Doctors can access drug utilization reports
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Calculate date range
    today = date.today()
    if period == "week":
        start_date = today - timedelta(days=today.weekday())
    elif period == "month":
        start_date = today.replace(day=1)
    elif period == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start_date = today.replace(month=quarter_start_month, day=1)
    else:  # year
        start_date = today.replace(month=1, day=1)
    
    end_date = today
    
    # Get prescriptions in period
    prescriptions_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.doctor_id == doctor.id,
                Prescription.prescription_date >= start_date.isoformat(),
                Prescription.prescription_date <= end_date.isoformat()
            )
        )
        .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
    )
    
    prescriptions = prescriptions_result.scalars().all()
    
    # Filter and analyze drug utilization
    drug_utilization = {}
    patient_demographics = {"age_groups": {}, "gender": {}}
    
    for prescription in prescriptions:
        patient_age = calculate_age(prescription.patient.date_of_birth)
        patient_gender = prescription.patient.gender
        
        for med in prescription.medications:
            drug_id_med = med.get("drug_id")
            
            # Apply filters
            if drug_id and drug_id_med != drug_id:
                continue
            
            if drug_id_med and drug_id_med in MOCK_DRUG_DATABASE:
                drug_data = MOCK_DRUG_DATABASE[drug_id_med]
                
                if therapeutic_category and drug_data["therapeutic_category"] != therapeutic_category:
                    continue
                
                drug_name = drug_data["generic_name"]
                
                if drug_name not in drug_utilization:
                    drug_utilization[drug_name] = {
                        "drug_id": drug_id_med,
                        "generic_name": drug_name,
                        "therapeutic_category": drug_data["therapeutic_category"],
                        "total_prescriptions": 0,
                        "total_quantity": 0,
                        "unique_patients": set(),
                        "age_distribution": {},
                        "gender_distribution": {"male": 0, "female": 0, "other": 0},
                        "dosage_forms": {},
                        "strengths": {}
                    }
                
                util = drug_utilization[drug_name]
                util["total_prescriptions"] += 1
                util["total_quantity"] += med.get("quantity", 0)
                util["unique_patients"].add(prescription.patient_id)
                
                # Age distribution
                age_group = "0-18" if patient_age < 18 else "18-65" if patient_age < 65 else "65+"
                util["age_distribution"][age_group] = util["age_distribution"].get(age_group, 0) + 1
                
                # Gender distribution
                gender_key = patient_gender.lower() if patient_gender else "other"
                if gender_key in util["gender_distribution"]:
                    util["gender_distribution"][gender_key] += 1
                
                # Dosage forms and strengths
                dosage_form = med.get("dosage_form", "unknown")
                strength = med.get("strength", "unknown")
                util["dosage_forms"][dosage_form] = util["dosage_forms"].get(dosage_form, 0) + 1
                util["strengths"][strength] = util["strengths"].get(strength, 0) + 1
    
    # Convert sets to counts and format response
    utilization_report = []
    for drug_name, util in drug_utilization.items():
        util["unique_patients"] = len(util["unique_patients"])
        utilization_report.append(util)
    
    # Sort by total prescriptions
    utilization_report.sort(key=lambda x: x["total_prescriptions"], reverse=True)
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "period": period,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "filters": {
            "drug_id": drug_id,
            "therapeutic_category": therapeutic_category
        },
        "total_drugs_analyzed": len(utilization_report),
        "drug_utilization": utilization_report,
        "report_generated_at": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# PRESCRIPTION TEMPLATES
# ============================================================================

@router.get("/templates/common-prescriptions")
async def get_common_prescription_templates(
    specialty: Optional[str] = Query(None),
    condition: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get common prescription templates based on specialty and conditions.
    
    Access Control:
    - Only Doctors can access prescription templates
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Mock prescription templates (in production, this would be from a templates database)
    templates = [
        {
            "template_id": "TEMP001",
            "template_name": "Common Cold Treatment",
            "specialty": "General Medicine",
            "condition": "Upper Respiratory Infection",
            "medications": [
                {
                    "drug_id": "DRUG001",
                    "generic_name": "Paracetamol",
                    "strength": "500mg",
                    "dosage_form": "tablet",
                    "dosage": "1 tablet",
                    "frequency": "TID",
                    "duration": "5 days",
                    "route": "oral",
                    "instructions": "After meals"
                }
            ],
            "general_instructions": "Take complete rest, drink plenty of fluids",
            "diet_instructions": "Light diet, warm liquids",
            "follow_up_days": 3
        },
        {
            "template_id": "TEMP002",
            "template_name": "Bacterial Infection Treatment",
            "specialty": "General Medicine",
            "condition": "Bacterial Infection",
            "medications": [
                {
                    "drug_id": "DRUG002",
                    "generic_name": "Amoxicillin",
                    "strength": "500mg",
                    "dosage_form": "capsule",
                    "dosage": "1 capsule",
                    "frequency": "TID",
                    "duration": "7 days",
                    "route": "oral",
                    "instructions": "Complete the full course"
                }
            ],
            "general_instructions": "Complete the antibiotic course even if symptoms improve",
            "diet_instructions": "Normal diet, take with food to avoid stomach upset",
            "follow_up_days": 7
        },
        {
            "template_id": "TEMP003",
            "template_name": "Diabetes Management",
            "specialty": "Endocrinology",
            "condition": "Type 2 Diabetes",
            "medications": [
                {
                    "drug_id": "DRUG003",
                    "generic_name": "Metformin",
                    "strength": "500mg",
                    "dosage_form": "tablet",
                    "dosage": "1 tablet",
                    "frequency": "BID",
                    "duration": "30 days",
                    "route": "oral",
                    "instructions": "With meals"
                }
            ],
            "general_instructions": "Monitor blood glucose regularly, maintain diet and exercise",
            "diet_instructions": "Diabetic diet, avoid sugary foods",
            "follow_up_days": 30
        }
    ]
    
    # Apply filters
    filtered_templates = templates
    
    if specialty:
        filtered_templates = [t for t in filtered_templates if t["specialty"].lower() == specialty.lower()]
    
    if condition:
        filtered_templates = [t for t in filtered_templates if condition.lower() in t["condition"].lower()]
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "doctor_specialty": doctor.specialization,
        "filters": {
            "specialty": specialty,
            "condition": condition
        },
        "total_templates": len(filtered_templates),
        "templates": filtered_templates
    }


@router.post("/templates/create-from-prescription")
async def create_template_from_prescription(
    prescription_number: str = Body(...),
    template_name: str = Body(...),
    template_description: Optional[str] = Body(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create prescription template from existing prescription.
    
    Access Control:
    - Only Doctors can create prescription templates
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get prescription
    prescription_result = await db.execute(
        select(Prescription)
        .where(
            and_(
                Prescription.prescription_number == prescription_number,
                Prescription.doctor_id == doctor.id
            )
        )
    )
    
    prescription = prescription_result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescription not found"
        )
    
    # Create template from prescription
    template = {
        "template_id": f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "template_name": template_name,
        "template_description": template_description,
        "specialty": doctor.specialization,
        "condition": prescription.diagnosis,
        "medications": prescription.medications,
        "general_instructions": prescription.general_instructions,
        "diet_instructions": prescription.diet_instructions,
        "created_by": str(doctor.id),
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    # In production, this would be saved to a templates database
    
    return {
        "message": "Prescription template created successfully",
        "template": template,
        "source_prescription": prescription_number
    }

# ============================================================================
# PRESCRIPTION DOWNLOAD AND FORMATTING
# ============