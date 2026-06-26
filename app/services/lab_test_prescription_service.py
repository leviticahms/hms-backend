import random
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import PrescriptionStatus, TestUrgency, UserRole
from app.core.role_aliases import normalize_staff_role_name
from app.models.lab import Equipment
from app.models.lab_portal import LabCatalogueTest
from app.models.patient import PatientProfile
from app.models.prescription import PrescriptionLabOrder, TelePrescription
from app.models.tenant import Hospital
from app.models.user import User
from app.services.prescription_pdf_service import generate_prescription_pdf


class LabTestPrescriptionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Search Lab Tests
    # ------------------------------------------------------------------

    async def search_tests(
        self,
        current_user: User,
        query: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> List[dict]:
        results: List[dict] = []

        # ── 1. Search lab catalogue tests (LabCatalogueTest) ─────────────────
        cat_stmt = select(LabCatalogueTest).where(
            LabCatalogueTest.status == "ACTIVE",
            LabCatalogueTest.hospital_id == current_user.hospital_id,
        )
        if query:
            q = f"%{query.strip().lower()}%"
            cat_stmt = cat_stmt.where(
                or_(
                    LabCatalogueTest.test_name.ilike(q),
                    LabCatalogueTest.test_code.ilike(q),
                    LabCatalogueTest.category.ilike(q),
                )
            )
        if category:
            c = category.strip().lower()
            if c not in ("all", "all tests"):
                cat_stmt = cat_stmt.where(LabCatalogueTest.category.ilike(c))

        cat_stmt = cat_stmt.order_by(desc(LabCatalogueTest.created_at))
        cat_result = await self.db.execute(cat_stmt)
        for test in cat_result.scalars().all():
            results.append({
                "test_id": test.id,
                "test_code": test.test_code,
                "test_name": test.test_name,
                "category": test.category,
                "sample_type": test.sample_type,
                "turnaround_time": test.turnaround_time,
                "price": float(test.price_inr) if test.price_inr is not None else None,
                "is_available": True,
            })

        # ── 2. Search lab equipment (Equipment) ───────────────────────────────
        eq_stmt = select(Equipment).where(
            Equipment.hospital_id == current_user.hospital_id,
            Equipment.status == "ACTIVE",
            Equipment.is_active == True,
        )
        if query:
            q = f"%{query.strip().lower()}%"
            eq_stmt = eq_stmt.where(
                or_(
                    Equipment.name.ilike(q),
                    Equipment.equipment_code.ilike(q),
                    Equipment.category.ilike(q),
                    Equipment.manufacturer.ilike(q),
                )
            )
        if category:
            c = category.strip().lower()
            if c not in ("all", "all tests"):
                eq_stmt = eq_stmt.where(Equipment.category.ilike(c))

        eq_stmt = eq_stmt.order_by(desc(Equipment.created_at))
        eq_result = await self.db.execute(eq_stmt)
        for eq in eq_result.scalars().all():
            results.append({
                "test_id": eq.id,
                "test_code": eq.equipment_code,
                "test_name": eq.name,
                "category": eq.category,
                "sample_type": None,
                "turnaround_time": None,
                "price": None,
                "is_available": eq.status == "ACTIVE",
            })

        # ── 3. Apply pagination over merged results ────────────────────────────
        offset = (page - 1) * limit
        return results[offset: offset + limit]

    # ------------------------------------------------------------------
    # Create Prescription
    # ------------------------------------------------------------------

    async def create_prescription(
        self,
        current_user: User,
        request,
    ) -> dict:
        if not current_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current user is not attached to a hospital",
            )

        # Resolve patient by human-readable ref (e.g. "PAT-ALEX-997")
        patient = await self._get_patient_by_ref(current_user, request.patient_ref)
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient '{request.patient_ref}' not found in this hospital",
            )

        if not request.tests:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one test is required",
            )

        prescription = TelePrescription(
            hospital_id=current_user.hospital_id,
            doctor_id=current_user.id,
            patient_id=patient.id,
            diagnosis=request.clinical_notes or "Lab test prescription",
            clinical_notes=request.clinical_notes,
            status=PrescriptionStatus.DRAFT,
            prescription_no=await self._generate_prescription_no(),
        )
        self.db.add(prescription)
        await self.db.flush()

        created_orders: List[PrescriptionLabOrder] = []
        for item in request.tests:
            lab_item = await self._get_lab_test(item.test_id)
            if not lab_item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Lab test '{item.test_name or item.test_id}' not found",
                )

            lab_order = PrescriptionLabOrder(
                hospital_id=current_user.hospital_id,
                prescription_id=prescription.id,
                lab_test_id=item.test_id,
                # Use DB name; fall back to what the doctor sent if somehow blank
                test_name=lab_item.test_name or item.test_name or "",
                test_code=lab_item.test_code,
                test_category=lab_item.category,
                clinical_notes=self._combine_item_notes(item.instructions, item.remarks),
                urgency=TestUrgency.ROUTINE,
            )
            self.db.add(lab_order)
            created_orders.append(lab_order)

        await self.db.commit()
        await self.db.refresh(prescription)

        first_order = created_orders[0]

        return {
            "success": True,
            "message": "Lab test prescription created successfully",
            "data": {
                "prescription_id": prescription.id,
                "test_id": first_order.lab_test_id,
                "test_name": first_order.test_name,
                "patient_ref": request.patient_ref,
                "status": prescription.status,
                "total_tests": len(created_orders),
                "created_at": prescription.created_at,
            },
        }

    # ------------------------------------------------------------------
    # Doctor Prescription List
    # ------------------------------------------------------------------

    async def get_doctor_prescriptions(
        self,
        current_user: User,
        patient_ref: Optional[str] = None,
        prescription_status: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        stmt = (
            select(TelePrescription)
            .where(TelePrescription.doctor_id == current_user.id)
            .options(
                selectinload(TelePrescription.patient).selectinload(PatientProfile.user),
                selectinload(TelePrescription.lab_orders),
            )
        )

        count_stmt = select(TelePrescription).where(TelePrescription.doctor_id == current_user.id)

        if patient_ref:
            stmt = stmt.join(
                PatientProfile, TelePrescription.patient_id == PatientProfile.id
            ).where(PatientProfile.patient_id == patient_ref)
            count_stmt = count_stmt.join(
                PatientProfile, TelePrescription.patient_id == PatientProfile.id
            ).where(PatientProfile.patient_id == patient_ref)

        if prescription_status:
            stmt = stmt.where(TelePrescription.status == prescription_status)
            count_stmt = count_stmt.where(TelePrescription.status == prescription_status)

        total_result = await self.db.execute(count_stmt)
        total = len(total_result.scalars().all())

        stmt = stmt.order_by(desc(TelePrescription.created_at)).limit(limit).offset((page - 1) * limit)
        result = await self.db.execute(stmt)
        prescriptions = result.scalars().all()

        return {
            "success": True,
            "message": "Doctor prescriptions retrieved successfully",
            "data": {
                "total": total,
                "page": page,
                "limit": limit,
                "prescriptions": [self._prescription_summary(rx) for rx in prescriptions],
            },
        }

    # ------------------------------------------------------------------
    # Get Prescription (doctor)
    # ------------------------------------------------------------------

    async def get_prescription(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> dict:
        prescription = await self._load_prescription_for_doctor(current_user, prescription_id)
        return {
            "success": True,
            "message": "Prescription retrieved successfully",
            "data": self._prescription_detail(prescription),
        }

    # ------------------------------------------------------------------
    # Update Prescription
    # ------------------------------------------------------------------

    async def update_prescription(
        self,
        current_user: User,
        prescription_id: UUID,
        request,
    ) -> dict:
        prescription = await self._load_prescription_for_doctor(current_user, prescription_id)

        if request.clinical_notes is not None:
            prescription.clinical_notes = request.clinical_notes

        if request.tests is not None:
            if not request.tests:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one test is required",
                )
            await self._replace_lab_orders(current_user.hospital_id, prescription, request.tests)

        await self.db.commit()
        await self.db.refresh(prescription)

        patient_ref = (
            prescription.patient.patient_id
            if prescription.patient
            else str(prescription.patient_id)
        )
        test_names = [order.test_name for order in (prescription.lab_orders or [])]

        return {
            "success": True,
            "message": "Prescription updated successfully",
            "data": {
                "prescription_id": prescription.id,
                "patient_ref": patient_ref,
                "status": prescription.status,
                "total_tests": len(test_names),
                "test_names": test_names,
                "updated_at": prescription.updated_at,
            },
        }

    # ------------------------------------------------------------------
    # Cancel Prescription
    # ------------------------------------------------------------------

    async def cancel_prescription(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> dict:
        prescription = await self._load_prescription_for_doctor(current_user, prescription_id)
        prescription.status = PrescriptionStatus.CANCELLED
        prescription.cancelled_at = datetime.utcnow()
        prescription.cancelled_by = current_user.id
        await self.db.commit()
        await self.db.refresh(prescription)

        return {
            "success": True,
            "message": "Prescription cancelled successfully",
            "data": {
                "prescription_id": prescription.id,
                "status": prescription.status,
                "updated_at": prescription.updated_at,
            },
        }

    # ------------------------------------------------------------------
    # Patient Prescription List
    # ------------------------------------------------------------------

    async def get_patient_prescriptions(
        self,
        current_user: User,
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        patient = await self._get_patient_profile_by_user(current_user)
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient profile not found",
            )

        count_result = await self.db.execute(
            select(TelePrescription).where(TelePrescription.patient_id == patient.id)
        )
        total = len(count_result.scalars().all())

        result = await self.db.execute(
            select(TelePrescription)
            .where(TelePrescription.patient_id == patient.id)
            .options(
                selectinload(TelePrescription.patient),
                selectinload(TelePrescription.doctor),
                selectinload(TelePrescription.lab_orders),
            )
            .order_by(desc(TelePrescription.created_at))
            .limit(limit)
            .offset((page - 1) * limit)
        )
        prescriptions = result.scalars().all()

        return {
            "success": True,
            "message": "Patient prescriptions retrieved successfully",
            "data": {
                "total": total,
                "page": page,
                "limit": limit,
                "prescriptions": [self._prescription_summary(rx) for rx in prescriptions],
            },
        }

    # ------------------------------------------------------------------
    # Get Prescription Detail (shared access)
    # ------------------------------------------------------------------

    async def get_prescription_detail(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> dict:
        prescription = await self._load_prescription_for_user(current_user, prescription_id)
        return {
            "success": True,
            "message": "Prescription detail retrieved successfully",
            "data": self._prescription_detail(prescription),
        }

    # ------------------------------------------------------------------
    # Generate PDF
    # ------------------------------------------------------------------

    async def generate_pdf(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> bytes:
        prescription = await self._load_prescription_for_user(current_user, prescription_id)

        hospital = await self._get_hospital(current_user.hospital_id)
        hospital_info = {
            "name": hospital.name if hospital else "Hospital",
            "address": hospital.address if hospital else "",
            "city": hospital.city if hospital else "",
            "state": hospital.state if hospital else "",
            "pincode": hospital.pincode if hospital else "",
            "phone": hospital.phone if hospital else "",
            "email": hospital.email if hospital else "",
        }

        patient_name = f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}"
        doctor_name = f"Dr. {prescription.doctor.first_name} {prescription.doctor.last_name}"

        medications = [
            {
                "generic_name": order.test_name,
                "strength": order.test_code,
                "dosage": "",
                "frequency": order.urgency,
                "duration_days": None,
                "instructions": order.clinical_notes,
                "quantity": None,
            }
            for order in (prescription.lab_orders or [])
        ]

        return generate_prescription_pdf(
            hospital=hospital_info,
            doctor_name=doctor_name,
            patient_name=patient_name,
            patient_ref=prescription.patient.patient_id,
            prescription_number=prescription.prescription_no,
            prescription_id=str(prescription.id),
            prescription_date=prescription.created_at.date().isoformat() if prescription.created_at else "",
            diagnosis=prescription.diagnosis,
            medications=medications,
            general_instructions=prescription.clinical_notes,
            diet_instructions=None,
            follow_up_date=prescription.follow_up_date,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_prescription_no(self) -> str:
        """Generates a unique prescription number like RX-2026-065445,
        matching the existing convention used elsewhere in the system."""
        year = datetime.utcnow().year
        for _ in range(10):
            candidate = f"RX-{year}-{random.randint(0, 999999):06d}"
            result = await self.db.execute(
                select(TelePrescription.id).where(TelePrescription.prescription_no == candidate)
            )
            if result.scalar_one_or_none() is None:
                return candidate
        # Extremely unlikely fallback: fall back to a longer random suffix
        return f"RX-{year}-{random.randint(0, 999999999):09d}"

    async def _get_patient_by_ref(
        self,
        current_user: User,
        patient_ref: str,
    ) -> Optional[PatientProfile]:
        """Look up a PatientProfile by human-readable patient_id (e.g. 'PAT-ALEX-997')."""
        result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == current_user.hospital_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_patient_profile_by_user(self, current_user: User) -> Optional[PatientProfile]:
        result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.user_id == current_user.id,
                    PatientProfile.hospital_id == current_user.hospital_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def _get_lab_test(self, test_id: UUID):
        """Look up a test by ID — checks LabCatalogueTest first, then Equipment."""
        result = await self.db.execute(
            select(LabCatalogueTest).where(LabCatalogueTest.id == test_id)
        )
        item = result.scalar_one_or_none()
        if item:
            return item

        # Also check lab equipment (e.g. ECG machine selected from search results)
        eq_result = await self.db.execute(
            select(Equipment).where(Equipment.id == test_id)
        )
        eq = eq_result.scalar_one_or_none()
        if eq:
            # Wrap equipment into a duck-typed object the caller expects
            class _EquipmentProxy:
                def __init__(self, e):
                    self.test_name = e.name
                    self.test_code = e.equipment_code
                    self.category = e.category
            return _EquipmentProxy(eq)

        return None

    async def _get_hospital(self, hospital_id: Optional[UUID]) -> Optional[Hospital]:
        if not hospital_id:
            return None
        result = await self.db.execute(select(Hospital).where(Hospital.id == hospital_id))
        return result.scalar_one_or_none()

    async def _load_prescription_for_doctor(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> TelePrescription:
        result = await self.db.execute(
            select(TelePrescription)
            .where(
                and_(
                    TelePrescription.id == prescription_id,
                    TelePrescription.doctor_id == current_user.id,
                )
            )
            .options(
                selectinload(TelePrescription.patient).selectinload(PatientProfile.user),
                selectinload(TelePrescription.lab_orders),
                selectinload(TelePrescription.doctor),
            )
        )
        prescription = result.scalar_one_or_none()
        if not prescription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Prescription not found",
            )
        return prescription

    async def _load_prescription_for_user(
        self,
        current_user: User,
        prescription_id: UUID,
    ) -> TelePrescription:
        result = await self.db.execute(
            select(TelePrescription)
            .where(TelePrescription.id == prescription_id)
            .options(
                selectinload(TelePrescription.patient).selectinload(PatientProfile.user),
                selectinload(TelePrescription.lab_orders),
                selectinload(TelePrescription.doctor),
            )
        )
        prescription = result.scalar_one_or_none()
        if not prescription:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prescription not found")

        if prescription.doctor_id == current_user.id:
            return prescription

        patient = await self._get_patient_profile_by_user(current_user)
        if patient and patient.id == prescription.patient_id:
            return prescription

        if self._user_has_any_role(current_user, (UserRole.LAB_TECH, UserRole.HOSPITAL_ADMIN)):
            return prescription

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    def _user_has_any_role(self, current_user: User, required_roles) -> bool:
        """Mirrors app.dependencies.auth.require_roles' role-checking logic exactly,
        so service-layer access checks stay consistent with router-layer ones."""
        raw = [getattr(role, "name", None) for role in (current_user.roles or [])]
        raw = [str(r).strip() for r in raw if r]
        user_roles_norm = {normalize_staff_role_name(r) for r in raw if r}

        required_role_names = [role.value for role in required_roles]
        return any(req in user_roles_norm for req in required_role_names)

    async def _replace_lab_orders(
        self,
        hospital_id: UUID,
        prescription: TelePrescription,
        tests,
    ) -> None:
        for order in prescription.lab_orders or []:
            await self.db.delete(order)

        await self.db.flush()

        for item in tests:
            lab_item = await self._get_lab_test(item.test_id)
            if not lab_item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Lab test not found: {item.test_id}",
                )

            new_order = PrescriptionLabOrder(
                hospital_id=hospital_id,
                prescription_id=prescription.id,
                lab_test_id=item.test_id,
                test_name=lab_item.test_name,
                test_code=lab_item.test_code,
                test_category=lab_item.category,
                clinical_notes=self._combine_item_notes(item.instructions, item.remarks),
                urgency=TestUrgency.ROUTINE,
            )
            self.db.add(new_order)

    def _combine_item_notes(self, instructions: Optional[str], remarks: Optional[str]) -> Optional[str]:
        if instructions and remarks:
            return f"{instructions}. {remarks}"
        return instructions or remarks

    def _prescription_summary(self, prescription: TelePrescription) -> dict:
        """Maps to schema.PrescriptionSummary"""
        patient_ref = (
            prescription.patient.patient_id
            if prescription.patient
            else None
        )
        test_names = [order.test_name for order in (prescription.lab_orders or [])]
        return {
            "prescription_id": prescription.id,
            "patient_ref": patient_ref,
            "status": prescription.status,
            "test_names": test_names,
            "total_tests": len(test_names),
            "created_at": prescription.created_at,
        }

    def _prescription_detail(self, prescription: TelePrescription) -> dict:
        """Maps to schema.PrescriptionDetail"""
        patient_ref = (
            prescription.patient.patient_id
            if prescription.patient
            else str(prescription.patient_id)
        )
        return {
            "prescription_id": prescription.id,
            "patient_ref": patient_ref,
            "clinical_notes": prescription.clinical_notes,
            "status": prescription.status,
            "tests": [
                {
                    "test_id": order.lab_test_id,
                    "test_name": order.test_name,
                    "instructions": order.clinical_notes,
                    "remarks": None,
                }
                for order in (prescription.lab_orders or [])
            ],
            "created_at": prescription.created_at,
            "updated_at": prescription.updated_at,
        }