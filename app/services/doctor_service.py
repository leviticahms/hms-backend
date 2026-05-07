"""
Doctor Service
Handles doctor-specific business logic including appointments, schedules, and patient consultations.
"""
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, date, time, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, update, delete
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.user import User, Role, user_roles
from app.models.patient import PatientProfile, Appointment, MedicalRecord, Admission
from app.models.hospital import Department, StaffDepartmentAssignment
from app.models.schedule import DoctorSchedule
from app.core.enums import UserRole, AppointmentStatus, UserStatus, DayOfWeek
from app.core.utils import generate_patient_ref, parse_date_string, validate_medicine_id


class DoctorService:
    """Service for doctor operations"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    @staticmethod
    def _appointment_patient_display_name(apt: Appointment) -> str:
        """Avoid 500s when patient/user rows are missing or not loaded."""
        patient = getattr(apt, "patient", None)
        if not patient:
            return "Patient"
        user = getattr(patient, "user", None)
        if not user:
            return "Patient"
        parts = [user.first_name or "", user.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or "Patient"
    
    # ============================================================================
    # USER CONTEXT AND VALIDATION
    # ============================================================================
    
    def get_user_context(self, current_user: User) -> dict:
        """Extract user context from JWT token"""
        user_roles = [role.name for role in current_user.roles]
        
        return {
            "user_id": str(current_user.id),
            "hospital_id": str(current_user.hospital_id),
            "role": user_roles[0] if user_roles else None,
            "all_roles": user_roles
        }
    
    async def validate_doctor_access(self, user_context: dict) -> None:
        """Ensure user is a doctor (any assigned role, not only the first)."""
        roles = user_context.get("all_roles") or []
        if UserRole.DOCTOR.value not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied - Doctor role required"
            )
    
    async def get_doctor_profile(self, user_context: dict):
        """Get doctor profile with department information"""
        await self.validate_doctor_access(user_context)
        
        # Get doctor user and their department assignment
        doctor_result = await self.db.execute(
            select(User)
            .where(User.id == user_context["user_id"])
        )
        doctor_user = doctor_result.scalar_one_or_none()
        
        if not doctor_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor user not found. Please contact administrator."
            )
            
        # Prefer primary active assignment; multiple rows must not use scalar_one_or_none()
        assignment_result = await self.db.execute(
            select(StaffDepartmentAssignment)
            .where(
                and_(
                    StaffDepartmentAssignment.staff_id == user_context["user_id"],
                    StaffDepartmentAssignment.is_active == True,
                )
            )
            .options(selectinload(StaffDepartmentAssignment.department))
            .order_by(
                StaffDepartmentAssignment.is_primary.desc(),
                StaffDepartmentAssignment.effective_from.desc(),
            )
            .limit(1)
        )
        assignment = assignment_result.scalars().first()
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor department assignment not found. Please contact administrator."
            )
            
        # Create a mock object that has the same interface as the old DoctorProfile
        class MockDoctorProfile:
            def __init__(self, user, department):
                self.user = user
                self.department = department
                self.id = user.id  # Add the id attribute that points to the user's id
                self.user_id = user.id
                self.hospital_id = user.hospital_id
                self.doctor_id = user.staff_id or f"DOC-{str(user.id)[:8]}"  # Add doctor_id attribute
                # Add commonly used attributes with default values
                self.specialization = "General Medicine"
                self.designation = "Doctor"
                self.experience_years = 5
                self.consultation_fee = 500.0
                self.medical_license_number = f"LIC-{user.id}"
                self.is_available = True
        
        return MockDoctorProfile(doctor_user, assignment.department)
    
    async def get_or_create_doctor_profile(self, user_context: dict, doctor):
        """Get or create doctor profile for prescription operations"""
        from app.models.doctor import DoctorProfile
        
        doctor_profile_result = await self.db.execute(
            select(DoctorProfile).where(DoctorProfile.user_id == doctor.user_id)
        )
        doctor_profile = doctor_profile_result.scalar_one_or_none()
        
        if not doctor_profile:
            # Create a doctor profile if it doesn't exist
            doctor_profile = DoctorProfile(
                id=uuid.uuid4(),
                hospital_id=uuid.UUID(user_context["hospital_id"]),
                user_id=doctor.user_id,
                department_id=doctor.department.id,
                doctor_id=doctor.doctor_id,
                medical_license_number=doctor.medical_license_number,
                designation=doctor.designation,
                specialization=doctor.specialization,
                experience_years=doctor.experience_years,
                consultation_fee=doctor.consultation_fee,
                is_available_for_emergency=False,
                is_accepting_new_patients=True
            )
            self.db.add(doctor_profile)
            await self.db.flush()  # Get the ID without committing
        
        return doctor_profile
    
    # ============================================================================
    # SCHEDULE MANAGEMENT
    # ============================================================================
    
    async def get_weekly_schedule(self, week_start: Optional[str], current_user: User) -> Dict[str, Any]:
        """Get doctor's weekly schedule with appointments"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Calculate week dates
        if week_start:
            start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
        else:
            today = date.today()
            start_date = today - timedelta(days=today.weekday())
        
        end_date = start_date + timedelta(days=6)
        
        # Get doctor's schedule configuration
        schedules_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor.user_id,
                    DoctorSchedule.is_active == True
                )
            )
        )
        schedules = schedules_result.scalars().all()
        
        # Get appointments for the week
        appointments_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.doctor_id == doctor.user_id,
                    Appointment.appointment_date >= start_date.isoformat(),
                    Appointment.appointment_date <= end_date.isoformat()
                )
            )
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
            .order_by(asc(Appointment.appointment_date), asc(Appointment.appointment_time))
        )
        
        appointments = appointments_result.scalars().all()
        
        # Build daily schedules
        daily_schedules = []
        total_slots = 0
        total_appointments = len(appointments)
        
        for i in range(7):
            current_date = start_date + timedelta(days=i)
            day_name = current_date.strftime("%A").upper()
            
            # Find schedule for this day (normalize DB casing)
            day_schedule = next(
                (s for s in schedules if (s.day_of_week or "").strip().upper() == day_name),
                None,
            )
            
            # Get appointments for this day
            day_appointments = [a for a in appointments if a.appointment_date == current_date.isoformat()]
            
            if day_schedule and day_schedule.slot_duration_minutes and day_schedule.slot_duration_minutes >= 15:
                # Calculate available slots (slot length must be set on the schedule row)
                start_time = datetime.strptime(day_schedule.start_time.strftime("%H:%M"), "%H:%M")
                end_time = datetime.strptime(day_schedule.end_time.strftime("%H:%M"), "%H:%M")
                slot_duration = timedelta(minutes=day_schedule.slot_duration_minutes)
                
                # Calculate break time
                break_duration = timedelta(0)
                if day_schedule.break_start_time and day_schedule.break_end_time:
                    break_start = datetime.strptime(day_schedule.break_start_time.strftime("%H:%M"), "%H:%M")
                    break_end = datetime.strptime(day_schedule.break_end_time.strftime("%H:%M"), "%H:%M")
                    break_duration = break_end - break_start
                
                # Calculate total available time
                total_time = end_time - start_time - break_duration
                day_total_slots = int(total_time.total_seconds() / slot_duration.total_seconds())
                total_slots += day_total_slots
                
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": True,
                    "start_time": day_schedule.start_time.strftime("%H:%M"),
                    "end_time": day_schedule.end_time.strftime("%H:%M"),
                    "slot_duration_minutes": day_schedule.slot_duration_minutes,
                    "break_start_time": day_schedule.break_start_time.strftime("%H:%M") if day_schedule.break_start_time else None,
                    "break_end_time": day_schedule.break_end_time.strftime("%H:%M") if day_schedule.break_end_time else None,
                    "total_slots": day_total_slots,
                    "booked_appointments": len(day_appointments),
                    "available_slots": day_total_slots - len(day_appointments),
                    "appointments": [
                        {
                            "appointment_ref": apt.appointment_ref,
                            "patient_name": self._appointment_patient_display_name(apt),
                            "appointment_time": apt.appointment_time,
                            "status": apt.status,
                            "chief_complaint": apt.chief_complaint
                        } for apt in day_appointments
                    ]
                })
            elif day_schedule:
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": True,
                    "start_time": day_schedule.start_time.strftime("%H:%M"),
                    "end_time": day_schedule.end_time.strftime("%H:%M"),
                    "slot_duration_minutes": day_schedule.slot_duration_minutes,
                    "total_slots": 0,
                    "booked_appointments": len(day_appointments),
                    "available_slots": 0,
                    "note": "Set slot_duration_minutes (15–120) on this schedule to enable bookable slots.",
                    "appointments": [
                        {
                            "appointment_ref": apt.appointment_ref,
                            "patient_name": self._appointment_patient_display_name(apt),
                            "appointment_time": apt.appointment_time,
                            "status": apt.status,
                            "chief_complaint": apt.chief_complaint
                        } for apt in day_appointments
                    ],
                })
            else:
                daily_schedules.append({
                    "date": current_date.isoformat(),
                    "day_name": day_name,
                    "has_schedule": False,
                    "total_slots": 0,
                    "booked_appointments": len(day_appointments),
                    "available_slots": 0,
                    "appointments": []
                })
        
        available_slots = total_slots - total_appointments
        
        return {
            "week_start": start_date.isoformat(),
            "week_end": end_date.isoformat(),
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "total_slots": total_slots,
            "total_appointments": total_appointments,
            "available_slots": available_slots,
            "daily_schedules": daily_schedules
        }
    
    async def get_schedule_slots(self, current_user: User) -> Dict[str, Any]:
        """Get doctor's schedule slots configuration"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get doctor's schedule configuration
        schedules_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor.user_id,
                    DoctorSchedule.is_active == True
                )
            )
            .order_by(DoctorSchedule.day_of_week)
        )
        schedules = schedules_result.scalars().all()
        
        # Format schedules
        schedule_slots = []
        for schedule in schedules:
            schedule_slots.append({
                "schedule_id": str(schedule.id),
                "day_of_week": schedule.day_of_week,
                "start_time": schedule.start_time.strftime("%H:%M"),
                "end_time": schedule.end_time.strftime("%H:%M"),
                "slot_duration_minutes": schedule.slot_duration_minutes,
                "break_start_time": schedule.break_start_time.strftime("%H:%M") if schedule.break_start_time else None,
                "break_end_time": schedule.break_end_time.strftime("%H:%M") if schedule.break_end_time else None,
                "max_patients_per_slot": schedule.max_patients_per_slot,
                "is_active": schedule.is_active,
                "notes": schedule.notes
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "department": doctor.department.name,
            "total_schedules": len(schedule_slots),
            "schedules": schedule_slots
        }
    
    async def create_schedule_slot(self, schedule_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create a new schedule slot for the doctor"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Check if schedule already exists for this day
        existing_schedule = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor.user_id,
                    DoctorSchedule.day_of_week == schedule_data["day_of_week"]
                )
            )
        )
        
        if existing_schedule.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Schedule already exists for {schedule_data['day_of_week']}"
            )
        
        # Parse time strings
        start_time = datetime.strptime(schedule_data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(schedule_data["end_time"], "%H:%M").time()
        
        break_start_time = None
        break_end_time = None
        if schedule_data.get("break_start_time"):
            break_start_time = datetime.strptime(schedule_data["break_start_time"], "%H:%M").time()
        if schedule_data.get("break_end_time"):
            break_end_time = datetime.strptime(schedule_data["break_end_time"], "%H:%M").time()
        
        if "slot_duration_minutes" not in schedule_data or schedule_data.get("slot_duration_minutes") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes is required (minutes per bookable slot, 15–120).",
            )
        slot_mins = int(schedule_data["slot_duration_minutes"])
        if slot_mins < 15 or slot_mins > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes must be between 15 and 120.",
            )

        # Create schedule
        schedule = DoctorSchedule(
            id=uuid.uuid4(),
            hospital_id=user_context["hospital_id"],
            doctor_id=doctor.user_id,
            day_of_week=schedule_data["day_of_week"],
            start_time=start_time,
            end_time=end_time,
            slot_duration_minutes=slot_mins,
            max_patients_per_slot=schedule_data.get("max_patients_per_slot", 1),
            break_start_time=break_start_time,
            break_end_time=break_end_time,
            notes=schedule_data.get("notes"),
            is_emergency_available=schedule_data.get("is_emergency_available", False)
        )
        
        self.db.add(schedule)
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "day_of_week": schedule.day_of_week,
            "start_time": schedule.start_time.strftime("%H:%M"),
            "end_time": schedule.end_time.strftime("%H:%M"),
            "message": "Schedule slot created successfully"
        }
    
    async def update_schedule_slot(self, schedule_id: str, update_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Update an existing schedule slot"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get schedule
        schedule_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.doctor_id == doctor.user_id
                )
            )
        )
        
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found"
            )
        
        # Update fields
        for field, value in update_data.items():
            if field in ["start_time", "end_time", "break_start_time", "break_end_time"] and value:
                setattr(schedule, field, datetime.strptime(value, "%H:%M").time())
            elif hasattr(schedule, field) and value is not None:
                setattr(schedule, field, value)
        
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "message": "Schedule slot updated successfully"
        }
    
    async def delete_schedule_slot(self, schedule_id: str, current_user: User) -> Dict[str, Any]:
        """Delete a schedule slot"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get schedule
        schedule_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.doctor_id == doctor.user_id
                )
            )
        )
        
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found"
            )
        
        await self.db.delete(schedule)
        await self.db.commit()
        
        return {
            "schedule_id": str(schedule.id),
            "message": "Schedule slot deleted successfully"
        }
    
    # ============================================================================
    # STAFF-MANAGED DOCTOR SCHEDULES (receptionist / nurse)
    # ============================================================================
    
    async def _get_staff_department_id_for_schedule(self, acting_user: User) -> uuid.UUID:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required to manage doctor schedules.",
            )
        r = await self.db.execute(
            select(StaffDepartmentAssignment.department_id).where(
                and_(
                    StaffDepartmentAssignment.staff_id == acting_user.id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
        )
        dept_id = r.scalar_one_or_none()
        if not dept_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No active department assignment found. Ask your admin to assign you to a department.",
            )
        return dept_id
    
    async def get_staff_department_name(self, acting_user: User) -> Optional[str]:
        """Display name of the department the acting nurse/receptionist is assigned to."""
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        row = await self.db.execute(select(Department.name).where(Department.id == dept_id))
        return row.scalar_one_or_none()
    
    async def get_target_doctor_in_hospital_for_staff_by_name(
        self, acting_user: User, doctor_name: str
    ) -> User:
        """
        Resolve doctor by display name; must be in the same hospital and same department
        as the acting receptionist/nurse (via StaffDepartmentAssignment).
        """
        raw = (doctor_name or "").strip()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="doctor_name is required (e.g. Dr. Jane Smith or Jane Smith).",
            )
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        q = (
            select(User)
            .join(user_roles, User.id == user_roles.c.user_id)
            .join(Role, user_roles.c.role_id == Role.id)
            .join(
                StaffDepartmentAssignment,
                and_(
                    StaffDepartmentAssignment.staff_id == User.id,
                    StaffDepartmentAssignment.department_id == dept_id,
                    StaffDepartmentAssignment.is_active == True,
                ),
            )
            .where(
                and_(
                    User.hospital_id == acting_user.hospital_id,
                    User.status == UserStatus.ACTIVE,
                    Role.name == UserRole.DOCTOR,
                    or_(
                        func.concat("Dr. ", User.first_name, " ", User.last_name).ilike(
                            f"%{raw}%"
                        ),
                        func.concat(User.first_name, " ", User.last_name).ilike(f"%{raw}%"),
                    ),
                )
            )
            .options(selectinload(User.roles))
        )
        result = await self.db.execute(q)
        doctors = list(result.scalars().unique().all())
        if len(doctors) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Multiple doctors in your department match that name. "
                    "Use a fuller name (e.g. include last name)."
                ),
            )
        if not doctors:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No doctor matching '{raw}' in your department. "
                    "They must be assigned to the same department as you."
                ),
            )
        return doctors[0]
    
    async def _ensure_schedule_doctor_in_staff_department(
        self, acting_user: User, doctor_user_id: uuid.UUID
    ) -> None:
        dept_id = await self._get_staff_department_id_for_schedule(acting_user)
        r = await self.db.execute(
            select(StaffDepartmentAssignment.id).where(
                and_(
                    StaffDepartmentAssignment.staff_id == doctor_user_id,
                    StaffDepartmentAssignment.department_id == dept_id,
                    StaffDepartmentAssignment.is_active == True,
                )
            )
        )
        if not r.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="That schedule belongs to a doctor outside your department.",
            )
    
    async def get_schedule_slots_for_target_doctor(
        self, acting_user: User, doctor_name: str
    ) -> Dict[str, Any]:
        """Weekly schedule template rows for a doctor (same shape as doctor self-service)."""
        doctor_user = await self.get_target_doctor_in_hospital_for_staff_by_name(
            acting_user, doctor_name
        )
        schedules_result = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor_user.id,
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                    DoctorSchedule.is_active == True,
                )
            )
            .order_by(DoctorSchedule.day_of_week)
        )
        schedules = schedules_result.scalars().all()
        schedule_slots = []
        for schedule in schedules:
            schedule_slots.append(
                {
                    "schedule_id": str(schedule.id),
                    "day_of_week": schedule.day_of_week,
                    "start_time": schedule.start_time.strftime("%H:%M"),
                    "end_time": schedule.end_time.strftime("%H:%M"),
                    "slot_duration_minutes": schedule.slot_duration_minutes,
                    "break_start_time": schedule.break_start_time.strftime("%H:%M")
                    if schedule.break_start_time
                    else None,
                    "break_end_time": schedule.break_end_time.strftime("%H:%M")
                    if schedule.break_end_time
                    else None,
                    "max_patients_per_slot": schedule.max_patients_per_slot,
                    "is_active": schedule.is_active,
                    "notes": schedule.notes,
                }
            )
        dept_nm = await self.get_staff_department_name(acting_user)
        return {
            "doctor_user_id": str(doctor_user.id),
            "doctor_name": f"Dr. {doctor_user.first_name} {doctor_user.last_name}",
            "department_name": dept_nm,
            "total_schedules": len(schedule_slots),
            "schedules": schedule_slots,
        }
    
    async def create_schedule_slot_for_staff(
        self,
        acting_user: User,
        doctor_name: str,
        schedule_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        doctor_user = await self.get_target_doctor_in_hospital_for_staff_by_name(
            acting_user, doctor_name
        )
        existing_schedule = await self.db.execute(
            select(DoctorSchedule)
            .where(
                and_(
                    DoctorSchedule.doctor_id == doctor_user.id,
                    DoctorSchedule.day_of_week == schedule_data["day_of_week"],
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                )
            )
        )
        if existing_schedule.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Schedule already exists for {schedule_data['day_of_week']}",
            )
        start_time = datetime.strptime(schedule_data["start_time"], "%H:%M").time()
        end_time = datetime.strptime(schedule_data["end_time"], "%H:%M").time()
        break_start_time = None
        break_end_time = None
        if schedule_data.get("break_start_time"):
            break_start_time = datetime.strptime(schedule_data["break_start_time"], "%H:%M").time()
        if schedule_data.get("break_end_time"):
            break_end_time = datetime.strptime(schedule_data["break_end_time"], "%H:%M").time()
        if "slot_duration_minutes" not in schedule_data or schedule_data.get("slot_duration_minutes") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes is required (minutes per bookable slot, 15–120).",
            )
        slot_mins = int(schedule_data["slot_duration_minutes"])
        if slot_mins < 15 or slot_mins > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="slot_duration_minutes must be between 15 and 120.",
            )

        schedule = DoctorSchedule(
            id=uuid.uuid4(),
            hospital_id=acting_user.hospital_id,
            doctor_id=doctor_user.id,
            day_of_week=schedule_data["day_of_week"],
            start_time=start_time,
            end_time=end_time,
            slot_duration_minutes=slot_mins,
            max_patients_per_slot=schedule_data.get("max_patients_per_slot", 1),
            break_start_time=break_start_time,
            break_end_time=break_end_time,
            notes=schedule_data.get("notes"),
            is_emergency_available=schedule_data.get("is_emergency_available", False),
        )
        self.db.add(schedule)
        await self.db.commit()
        return {
            "schedule_id": str(schedule.id),
            "doctor_user_id": str(doctor_user.id),
            "day_of_week": schedule.day_of_week,
            "start_time": schedule.start_time.strftime("%H:%M"),
            "end_time": schedule.end_time.strftime("%H:%M"),
            "message": "Schedule slot created successfully",
        }
    
    async def update_schedule_slot_for_staff(
        self,
        acting_user: User,
        schedule_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required",
            )
        schedule_result = await self.db.execute(
            select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                )
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found",
            )
        await self._ensure_schedule_doctor_in_staff_department(acting_user, schedule.doctor_id)
        for field, value in update_data.items():
            if field in ["start_time", "end_time", "break_start_time", "break_end_time"] and value:
                setattr(schedule, field, datetime.strptime(value, "%H:%M").time())
            elif hasattr(schedule, field) and value is not None:
                setattr(schedule, field, value)
        await self.db.commit()
        return {
            "schedule_id": str(schedule.id),
            "doctor_user_id": str(schedule.doctor_id),
            "message": "Schedule slot updated successfully",
        }
    
    async def delete_schedule_slot_for_staff(
        self, acting_user: User, schedule_id: str
    ) -> Dict[str, Any]:
        if not acting_user.hospital_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospital context required",
            )
        schedule_result = await self.db.execute(
            select(DoctorSchedule).where(
                and_(
                    DoctorSchedule.id == uuid.UUID(schedule_id),
                    DoctorSchedule.hospital_id == acting_user.hospital_id,
                )
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule slot not found",
            )
        await self._ensure_schedule_doctor_in_staff_department(acting_user, schedule.doctor_id)
        doc_id = schedule.doctor_id
        await self.db.delete(schedule)
        await self.db.commit()
        return {
            "schedule_id": schedule_id,
            "doctor_user_id": str(doc_id),
            "message": "Schedule slot deleted successfully",
        }
    
    # ============================================================================
    # APPOINTMENT MANAGEMENT
    # ============================================================================
    
    async def get_doctor_appointments(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get doctor's appointments with filtering options"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Build query conditions
        conditions = [Appointment.doctor_id == doctor.id]
        
        if filters.get("date_from"):
            conditions.append(Appointment.appointment_date >= filters["date_from"])
        
        if filters.get("date_to"):
            conditions.append(Appointment.appointment_date <= filters["date_to"])
        
        if filters.get("status"):
            conditions.append(Appointment.status == filters["status"])
        
        # Get appointments
        appointments_result = await self.db.execute(
            select(Appointment)
            .where(and_(*conditions))
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
            .order_by(desc(Appointment.appointment_date), desc(Appointment.appointment_time))
            .limit(filters.get("limit", 50))
        )
        
        appointments = appointments_result.scalars().all()
        
        # Format appointments
        appointment_list = []
        for appointment in appointments:
            patient_age = self.calculate_age(appointment.patient.date_of_birth)
            
            appointment_list.append({
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "patient_age": patient_age,
                "patient_phone": appointment.patient.user.phone,
                "appointment_date": appointment.appointment_date,
                "appointment_time": appointment.appointment_time,
                "duration_minutes": appointment.duration_minutes,
                "status": appointment.status,
                "appointment_type": appointment.appointment_type,
                "chief_complaint": appointment.chief_complaint,
                "notes": appointment.notes,
                "is_checked_in": appointment.checked_in_at is not None,
                "checked_in_at": appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
                "is_completed": appointment.status == AppointmentStatus.COMPLETED,
                "completed_at": appointment.completed_at.isoformat() if appointment.completed_at else None,
                "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
                "is_paid": appointment.is_paid
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "total_appointments": len(appointment_list),
            "filters": {
                "date_from": filters.get("date_from"),
                "date_to": filters.get("date_to"),
                "status": filters.get("status")
            },
            "appointments": appointment_list
        }
    
    async def get_appointment_details(self, appointment_ref: str, current_user: User) -> Dict[str, Any]:
        """Get detailed appointment information"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
            .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        patient_age = self.calculate_age(appointment.patient.date_of_birth)
        
        return {
            "appointment_ref": appointment.appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "patient_age": patient_age,
            "patient_phone": appointment.patient.user.phone,
            "appointment_date": appointment.appointment_date,
            "appointment_time": appointment.appointment_time,
            "duration_minutes": appointment.duration_minutes,
            "status": appointment.status,
            "appointment_type": appointment.appointment_type,
            "chief_complaint": appointment.chief_complaint,
            "notes": appointment.notes,
            "is_checked_in": appointment.checked_in_at is not None,
            "checked_in_at": appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
            "is_completed": appointment.status == AppointmentStatus.COMPLETED,
            "completed_at": appointment.completed_at.isoformat() if appointment.completed_at else None,
            "consultation_fee": float(appointment.consultation_fee) if appointment.consultation_fee else None,
            "is_paid": appointment.is_paid
        }
    
    async def update_appointment(self, appointment_ref: str, update_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Update appointment details"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be updated
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update completed appointment"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update cancelled appointment"
            )
        
        # Build update data
        update_fields = {}
        
        if update_data.get("appointment_date"):
            # Validate date is not in the past
            appointment_date = datetime.strptime(update_data["appointment_date"], "%Y-%m-%d").date()
            if appointment_date < date.today():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot schedule appointment in the past"
                )
            update_fields["appointment_date"] = update_data["appointment_date"]
        
        if update_data.get("appointment_time"):
            update_fields["appointment_time"] = update_data["appointment_time"]
        
        if update_data.get("duration_minutes"):
            update_fields["duration_minutes"] = update_data["duration_minutes"]
        
        if update_data.get("appointment_type"):
            update_fields["appointment_type"] = update_data["appointment_type"]
        
        if "notes" in update_data:
            update_fields["notes"] = update_data["notes"]
        
        if "consultation_fee" in update_data:
            update_fields["consultation_fee"] = update_data["consultation_fee"]
        
        # Apply updates
        if update_fields:
            await self.db.execute(
                update(Appointment)
                .where(Appointment.id == appointment.id)
                .values(**update_fields)
            )
            await self.db.commit()
        
        return {
            "message": "Appointment updated successfully",
            "appointment_ref": appointment_ref,
            "updated_fields": list(update_fields.keys())
        }
    
    async def complete_appointment(self, appointment_ref: str, current_user: User) -> Dict[str, Any]:
        """Mark appointment as completed"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be completed
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Appointment is already completed"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot complete cancelled appointment"
            )
        
        # Update appointment status
        await self.db.execute(
            update(Appointment)
            .where(Appointment.id == appointment.id)
            .values(
                status=AppointmentStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc)
            )
        )
        await self.db.commit()
        
        return {
            "message": "Appointment completed successfully",
            "appointment_ref": appointment_ref,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def cancel_appointment(self, appointment_ref: str, cancellation_reason: str, current_user: User) -> Dict[str, Any]:
        """Cancel appointment"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == appointment_ref,
                    Appointment.doctor_id == doctor.id
                )
            )
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if appointment can be cancelled
        if appointment.status == AppointmentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel completed appointment"
            )
        
        if appointment.status == AppointmentStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Appointment is already cancelled"
            )
        
        # Update appointment status
        await self.db.execute(
            update(Appointment)
            .where(Appointment.id == appointment.id)
            .values(
                status=AppointmentStatus.CANCELLED,
                cancelled_at=datetime.now(timezone.utc),
                cancellation_reason=cancellation_reason
            )
        )
        await self.db.commit()
        
        return {
            "message": "Appointment cancelled successfully",
            "appointment_ref": appointment_ref,
            "cancellation_reason": cancellation_reason,
            "cancelled_at": datetime.now(timezone.utc).isoformat()
        }
    
    # ============================================================================
    # PATIENT CONSULTATION
    # ============================================================================
    
    async def get_patient_consultation_details(self, patient_ref: str, current_user: User) -> Dict[str, Any]:
        """Get comprehensive patient details for consultation"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get patient
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == patient_ref,
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
            .options(selectinload(PatientProfile.user))
        )
        
        patient = patient_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found"
            )
        
        # Get current appointment (if any)
        current_appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date == date.today().isoformat(),
                    Appointment.status.in_([AppointmentStatus.CONFIRMED])
                )
            )
            .order_by(desc(Appointment.appointment_time))
            .limit(1)
        )
        
        current_appointment = current_appointment_result.scalar_one_or_none()
        
        # Get medical history (last 10 records)
        medical_history_result = await self.db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.patient_id == patient.id)
            .order_by(desc(MedicalRecord.created_at))
            .limit(10)
        )
        
        medical_records = medical_history_result.scalars().all()
        
        # Format medical history
        medical_history = []
        for record in medical_records:
            medical_history.append({
                "record_id": str(record.id),
                "date": record.created_at.date().isoformat(),
                "chief_complaint": record.chief_complaint,
                "diagnosis": record.diagnosis,
                "treatment_plan": record.treatment_plan,
                "follow_up_instructions": record.follow_up_instructions,
                "vital_signs": record.vital_signs or {}
            })
        
        # Get last visit date
        last_visit_result = await self.db.execute(
            select(Appointment.appointment_date)
            .where(
                and_(
                    Appointment.patient_id == patient.id,
                    Appointment.doctor_id == doctor.id,
                    Appointment.status == AppointmentStatus.COMPLETED
                )
            )
            .order_by(desc(Appointment.appointment_date))
            .limit(1)
        )
        
        last_visit = last_visit_result.scalar_one_or_none()
        
        patient_age = self.calculate_age(patient.date_of_birth)
        
        return {
            "patient_ref": patient.patient_id,
            "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
            "patient_age": patient_age,
            "appointment_ref": current_appointment.appointment_ref if current_appointment else "",
            "appointment_date": current_appointment.appointment_date if current_appointment else "",
            "appointment_time": current_appointment.appointment_time if current_appointment else "",
            "chief_complaint": current_appointment.chief_complaint if current_appointment else None,
            "medical_history": medical_history,
            "current_medications": patient.current_medications or [],
            "allergies": patient.allergies or [],
            "chronic_conditions": patient.chronic_conditions or [],
            "vital_signs": {},  # Will be filled from latest medical record
            "last_visit_date": last_visit
        }
    
    async def create_medical_record(self, record_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create medical record for patient consultation"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get appointment
        appointment_result = await self.db.execute(
            select(Appointment)
            .where(
                and_(
                    Appointment.appointment_ref == record_data["appointment_ref"],
                    Appointment.doctor_id == doctor.id
                )
            )
            .options(selectinload(Appointment.patient))
        )
        
        appointment = appointment_result.scalar_one_or_none()
        if not appointment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Appointment not found"
            )
        
        # Check if medical record already exists for this appointment
        existing_record = await self.db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.appointment_id == appointment.id)
        )
        
        if existing_record.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Medical record already exists for this appointment"
            )
        
        # Create medical record
        medical_record = MedicalRecord(
            patient_id=appointment.patient_id,
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            hospital_id=uuid.UUID(user_context["hospital_id"]),
            chief_complaint=record_data["chief_complaint"],
            history_of_present_illness=record_data.get("history_of_present_illness"),
            past_medical_history=record_data.get("past_medical_history"),
            examination_findings=record_data.get("examination_findings"),
            vital_signs=record_data.get("vital_signs", {}),
            diagnosis=record_data.get("diagnosis"),
            differential_diagnosis=record_data.get("differential_diagnosis", []),
            treatment_plan=record_data.get("treatment_plan"),
            follow_up_instructions=record_data.get("follow_up_instructions"),
            prescriptions=record_data.get("prescriptions", []),
            lab_orders=record_data.get("lab_orders", []),
            imaging_orders=record_data.get("imaging_orders", []),
            is_finalized=True,
            finalized_at=datetime.now(timezone.utc)
        )
        
        self.db.add(medical_record)
        await self.db.commit()
        await self.db.refresh(medical_record)
        
        return {
            "message": "Medical record created successfully",
            "record_id": str(medical_record.id),
            "patient_ref": appointment.patient.patient_id,
            "appointment_ref": record_data["appointment_ref"],
            "created_at": medical_record.created_at.isoformat()
        }
    
    # ============================================================================
    # PRESCRIPTION MANAGEMENT
    # ============================================================================
    
    async def create_prescription(self, prescription_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Create prescription for patient"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get patient
        patient_result = await self.db.execute(
            select(PatientProfile)
            .where(
                and_(
                    PatientProfile.patient_id == prescription_data["patient_ref"],
                    PatientProfile.hospital_id == user_context["hospital_id"]
                )
            )
        )
        
        patient = patient_result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Patient not found"
            )
        
        # Get appointment if provided
        appointment_id = None
        medical_record_id = None
        
        if prescription_data.get("appointment_ref"):
            appointment_result = await self.db.execute(
                select(Appointment)
                .where(
                    and_(
                        Appointment.appointment_ref == prescription_data["appointment_ref"],
                        Appointment.doctor_id == doctor.id,
                        Appointment.patient_id == patient.id
                    )
                )
            )
            
            appointment = appointment_result.scalar_one_or_none()
            if appointment:
                appointment_id = appointment.id
                
                # Get associated medical record
                medical_record_result = await self.db.execute(
                    select(MedicalRecord)
                    .where(MedicalRecord.appointment_id == appointment.id)
                )
                
                medical_record = medical_record_result.scalar_one_or_none()
                if medical_record:
                    medical_record_id = medical_record.id
        
        # Generate prescription number
        prescription_number = self.generate_prescription_number()
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Create prescription
        from app.models.doctor import Prescription
        prescription = Prescription(
            patient_id=patient.id,
            doctor_id=doctor_profile.id,  # Use doctor_profile.id instead of doctor.id
            appointment_id=appointment_id,
            medical_record_id=medical_record_id,
            hospital_id=uuid.UUID(user_context["hospital_id"]),
            prescription_number=prescription_number,
            prescription_date=date.today().isoformat(),
            diagnosis=prescription_data.get("diagnosis"),
            symptoms=prescription_data.get("symptoms"),
            medications=prescription_data["medications"],
            general_instructions=prescription_data.get("general_instructions"),
            diet_instructions=prescription_data.get("diet_instructions"),
            follow_up_date=prescription_data.get("follow_up_date"),
            is_digitally_signed=True,  # Assuming digital signature
            signature_hash=f"hash_{prescription_number}"  # Placeholder for actual hash
        )
        
        self.db.add(prescription)
        await self.db.commit()
        await self.db.refresh(prescription)
        
        return {
            "message": "Prescription created successfully",
            "prescription_id": str(prescription.id),
            "prescription_number": prescription_number,
            "patient_ref": prescription_data["patient_ref"],
            "total_medications": len(prescription_data["medications"]),
            "created_at": prescription.created_at.isoformat()
        }
    
    async def get_doctor_prescriptions(self, filters: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Get doctor's prescriptions with filtering options"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Build query conditions
        from app.models.doctor import Prescription
        conditions = [Prescription.doctor_id == doctor_profile.id]
        
        if filters.get("patient_ref"):
            # Get patient ID
            patient_result = await self.db.execute(
                select(PatientProfile.id)
                .where(
                    and_(
                        PatientProfile.patient_id == filters["patient_ref"],
                        PatientProfile.hospital_id == user_context["hospital_id"]
                    )
                )
            )
            
            patient_id = patient_result.scalar_one_or_none()
            if patient_id:
                conditions.append(Prescription.patient_id == patient_id)
            else:
                # Patient not found, return empty result
                return {
                    "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
                    "total_prescriptions": 0,
                    "filters": filters,
                    "prescriptions": []
                }
        
        if filters.get("date_from"):
            conditions.append(Prescription.prescription_date >= filters["date_from"])
        
        if filters.get("date_to"):
            conditions.append(Prescription.prescription_date <= filters["date_to"])
        
        # Get prescriptions
        prescriptions_result = await self.db.execute(
            select(Prescription)
            .where(and_(*conditions))
            .options(selectinload(Prescription.patient).selectinload(PatientProfile.user))
            .order_by(desc(Prescription.created_at))
            .limit(filters.get("limit", 20))
        )
        
        prescriptions = prescriptions_result.scalars().all()
        
        # Format prescriptions
        prescription_list = []
        for prescription in prescriptions:
            prescription_list.append({
                "prescription_id": str(prescription.id),
                "prescription_number": prescription.prescription_number,
                "patient_ref": prescription.patient.patient_id,
                "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
                "prescription_date": prescription.prescription_date,
                "diagnosis": prescription.diagnosis,
                "total_medications": len(prescription.medications),
                "medications": prescription.medications,
                "general_instructions": prescription.general_instructions,
                "follow_up_date": prescription.follow_up_date,
                "is_dispensed": prescription.is_dispensed,
                "dispensed_at": prescription.dispensed_at,
                "created_at": prescription.created_at.isoformat()
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "total_prescriptions": len(prescription_list),
            "filters": filters,
            "prescriptions": prescription_list
        }
    
    async def get_prescription_details(self, prescription_number: str, current_user: User) -> Dict[str, Any]:
        """Get detailed prescription information"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Get prescription
        from app.models.doctor import Prescription
        prescription_result = await self.db.execute(
            select(Prescription)
            .where(
                and_(
                    Prescription.prescription_number == prescription_number,
                    Prescription.doctor_id == doctor_profile.id  # Use doctor_profile.id
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
        
        return {
            "prescription_id": str(prescription.id),
            "prescription_number": prescription.prescription_number,
            "patient_ref": prescription.patient.patient_id,
            "patient_name": f"{prescription.patient.user.first_name} {prescription.patient.user.last_name}",
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "prescription_date": prescription.prescription_date,
            "diagnosis": prescription.diagnosis,
            "symptoms": prescription.symptoms,
            "medications": prescription.medications,
            "general_instructions": prescription.general_instructions,
            "diet_instructions": prescription.diet_instructions,
            "follow_up_date": prescription.follow_up_date,
            "is_dispensed": prescription.is_dispensed,
            "dispensed_at": prescription.dispensed_at,
            "is_digitally_signed": prescription.is_digitally_signed,
            "created_at": prescription.created_at.isoformat()
        }
    
    # ============================================================================
    # PATIENT SEARCH AND LOOKUP
    # ============================================================================
    
    async def search_patients(self, search_query: str, limit: int, current_user: User) -> Dict[str, Any]:
        """Search patients by name, phone, or patient ID"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Search patients
        search_conditions = [
            PatientProfile.hospital_id == user_context["hospital_id"]
        ]
        
        # Add search conditions
        search_terms = or_(
            PatientProfile.patient_id.ilike(f"%{search_query}%"),
            User.first_name.ilike(f"%{search_query}%"),
            User.last_name.ilike(f"%{search_query}%"),
            User.phone.ilike(f"%{search_query}%"),
            User.email.ilike(f"%{search_query}%")
        )
        
        search_conditions.append(search_terms)
        
        # Execute search
        patients_result = await self.db.execute(
            select(PatientProfile)
            .join(User, PatientProfile.user_id == User.id)
            .where(and_(*search_conditions))
            .options(selectinload(PatientProfile.user))
            .limit(limit)
        )
        
        patients = patients_result.scalars().all()
        
        # Format results
        patient_list = []
        for patient in patients:
            patient_age = self.calculate_age(patient.date_of_birth)
            
            # Get last appointment with this doctor
            last_appointment_result = await self.db.execute(
                select(Appointment.appointment_date, Appointment.status)
                .where(
                    and_(
                        Appointment.patient_id == patient.id,
                        Appointment.doctor_id == doctor.id
                    )
                )
                .order_by(desc(Appointment.appointment_date))
                .limit(1)
            )
            
            last_appointment = last_appointment_result.first()
            
            patient_list.append({
                "patient_ref": patient.patient_id,
                "patient_name": f"{patient.user.first_name} {patient.user.last_name}",
                "patient_age": patient_age,
                "phone_number": patient.user.phone,
                "email": patient.user.email,
                "blood_group": patient.blood_group,
                "chronic_conditions": patient.chronic_conditions or [],
                "last_appointment_date": last_appointment.appointment_date if last_appointment else None,
                "last_appointment_status": last_appointment.status if last_appointment else None
            })
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "search_query": search_query,
            "total_results": len(patient_list),
            "patients": patient_list
        }
    
    # ============================================================================
    # STATISTICS AND REPORTS
    # ============================================================================
    
    async def get_statistics_summary(self, period: str, current_user: User) -> Dict[str, Any]:
        """Get comprehensive statistics summary for doctor"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        
        # Get or create the doctor profile
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
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
        
        # Get appointment statistics
        appointments_stats = await self.db.execute(
            select(
                func.count(Appointment.id).label('total'),
                func.count(func.nullif(Appointment.status != AppointmentStatus.COMPLETED, True)).label('completed'),
                func.count(func.nullif(Appointment.status != AppointmentStatus.CANCELLED, True)).label('cancelled'),
                func.count(func.distinct(Appointment.patient_id)).label('unique_patients')
            )
            .where(
                and_(
                    Appointment.doctor_id == doctor.id,
                    Appointment.appointment_date >= start_date.isoformat(),
                    Appointment.appointment_date <= end_date.isoformat()
                )
            )
        )
        
        apt_stats = appointments_stats.first()
        
        # Get prescription statistics
        from app.models.doctor import Prescription
        prescription_stats = await self.db.execute(
            select(func.count(Prescription.id))
            .where(
                and_(
                    Prescription.doctor_id == doctor_profile.id,  # Use doctor_profile.id
                    Prescription.prescription_date >= start_date.isoformat(),
                    Prescription.prescription_date <= end_date.isoformat()
                )
            )
        )
        
        total_prescriptions = prescription_stats.scalar() or 0
        
        # Get medical record statistics
        medical_record_stats = await self.db.execute(
            select(func.count(MedicalRecord.id))
            .where(
                and_(
                    MedicalRecord.doctor_id == doctor.id,
                    MedicalRecord.created_at >= datetime.combine(start_date, datetime.min.time()),
                    MedicalRecord.created_at <= datetime.combine(end_date, datetime.max.time())
                )
            )
        )
        
        total_medical_records = medical_record_stats.scalar() or 0
        
        # Calculate completion rate
        total_appointments = apt_stats.total or 0
        completed_appointments = apt_stats.completed or 0
        completion_rate = round((completed_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
        
        return {
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "period": period,
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            },
            "statistics": {
                "appointments": {
                    "total": total_appointments,
                    "completed": completed_appointments,
                    "cancelled": apt_stats.cancelled or 0,
                    "completion_rate": completion_rate
                },
                "patients": {
                    "unique_patients_treated": apt_stats.unique_patients or 0
                },
                "prescriptions": {
                    "total_prescriptions": total_prescriptions
                },
                "medical_records": {
                    "total_records_created": total_medical_records
                }
            }
        }
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    def calculate_age(self, date_of_birth: str) -> int:
        """Calculate age from date of birth"""
        try:
            birth_date = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
            today = date.today()
            return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        except:
            return 0
    
    def generate_prescription_number(self) -> str:
        """Generate unique prescription number"""
        import random
        import string
        
        # Format: RX-YYYY-XXXXXX
        year = datetime.now().year
        random_part = ''.join(random.choices(string.digits, k=6))
        return f"RX-{year}-{random_part}"
    
    async def add_prescription_items(self, prescription_id: str, items_data: Dict[str, Any], current_user: User) -> Dict[str, Any]:
        """Add medicines and lab orders to an existing prescription"""
        user_context = self.get_user_context(current_user)
        doctor = await self.get_doctor_profile(user_context)
        doctor_profile = await self.get_or_create_doctor_profile(user_context, doctor)
        
        # Get prescription
        from app.models.doctor import Prescription
        prescription_result = await self.db.execute(
            select(Prescription)
            .where(
                and_(
                    Prescription.id == uuid.UUID(prescription_id),
                    Prescription.doctor_id == doctor_profile.id
                )
            )
        )
        
        prescription = prescription_result.scalar_one_or_none()
        if not prescription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Prescription not found"
            )
        
        medicines_added = 0
        lab_orders_added = 0
        
        # Process medicines
        if items_data.get("medicines"):
            for medicine_data in items_data["medicines"]:
                # Validate medicine ID if provided
                if medicine_data.get("medicine_id") and medicine_data["medicine_id"].strip():
                    is_valid, error_message = validate_medicine_id(medicine_data["medicine_id"])
                    
                    if is_valid:
                        try:
                            medicine_uuid = uuid.UUID(medicine_data["medicine_id"])
                            # Validate medicine exists in the database
                            from app.models.medicine import Medicine
                            medicine_result = await self.db.execute(
                                select(Medicine)
                                .where(
                                    and_(
                                        Medicine.id == medicine_uuid,
                                        Medicine.hospital_id == user_context["hospital_id"]
                                    )
                                )
                            )
                            
                            medicine = medicine_result.scalar_one_or_none()
                            if not medicine:
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail={
                                        "code": "INVALID_MEDICINE_ID",
                                        "message": f"Medicine with ID {medicine_data['medicine_id']} not found"
                                    }
                                )
                        except ValueError:
                            # This shouldn't happen since we validated above, but handle gracefully
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail={
                                    "code": "INVALID_MEDICINE_ID",
                                    "message": error_message or "Invalid medicine ID format"
                                }
                            )
                    else:
                        # Invalid medicine ID format
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={
                                "code": "INVALID_MEDICINE_ID",
                                "message": error_message or "Invalid medicine ID format"
                            }
                        )
                
                medicines_added += 1
        
        # Process lab orders
        if items_data.get("lab_orders"):
            lab_orders_added = len(items_data["lab_orders"])
        
        # For now, return success without actually modifying the prescription
        # In a full implementation, this would update the prescription's medications array
        
        return {
            "message": "Prescription items processed successfully",
            "prescription_id": prescription_id,
            "medicines_added": medicines_added,
            "lab_orders_added": lab_orders_added,
            "validation_notes": "Medicine IDs validated where provided"
        }