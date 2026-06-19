"""
Doctor Appointment Tracking and Notifications API
Comprehensive appointment tracking system with real-time notifications, automated reminders,
patient communication, and appointment lifecycle management for doctors.

BUSINESS RULES:
- Only Doctors can access their appointment tracking features
- Real-time appointment status updates and notifications
- Automated patient reminders and confirmations
- Department-based data filtering and hospital isolation
- Comprehensive appointment lifecycle tracking
- Multi-channel notification support (SMS, Email, Push)
"""
import uuid
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timedelta, date, time, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func, asc, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from enum import Enum

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.models.patient import PatientProfile, Appointment, MedicalRecord
from app.models.doctor import DoctorProfile
from app.models.schedule import DoctorSchedule
from app.models.hospital import Department
from app.core.enums import UserRole, AppointmentStatus
from app.core.utils import generate_patient_ref


router = APIRouter(prefix="/doctor-appointment-tracking", tags=["Doctor Portal - Appointment Tracking"])


def _parse_appointment_datetime(appointment_date: str, appointment_time: str) -> datetime:
    """Parse appointment date + time, handling both HH:MM and HH:MM:SS formats."""
    time_str = appointment_time
    if time_str.count(":") == 1:
        time_str += ":00"
    return datetime.strptime(f"{appointment_date} {time_str}", "%Y-%m-%d %H:%M:%S")


def _parse_appointment_time(appointment_time: str) -> time:
    """Parse appointment time string, handling both HH:MM and HH:MM:SS formats."""
    if appointment_time.count(":") == 1:
        appointment_time += ":00"
    return datetime.strptime(appointment_time, "%H:%M:%S").time()


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class NotificationType(str, Enum):
    """Notification types"""
    APPOINTMENT_REMINDER = "APPOINTMENT_REMINDER"
    APPOINTMENT_CONFIRMATION = "APPOINTMENT_CONFIRMATION"
    APPOINTMENT_CANCELLATION = "APPOINTMENT_CANCELLATION"
    APPOINTMENT_RESCHEDULE = "APPOINTMENT_RESCHEDULE"
    APPOINTMENT_CHECKIN = "APPOINTMENT_CHECKIN"
    APPOINTMENT_DELAY = "APPOINTMENT_DELAY"
    FOLLOW_UP_REMINDER = "FOLLOW_UP_REMINDER"
    PRESCRIPTION_READY = "PRESCRIPTION_READY"
    LAB_RESULTS_READY = "LAB_RESULTS_READY"


class NotificationChannel(str, Enum):
    """Notification delivery channels"""
    SMS = "SMS"
    EMAIL = "EMAIL"
    PUSH = "PUSH"
    IN_APP = "IN_APP"
    WHATSAPP = "WHATSAPP"


class AppointmentTrackingStatus(str, Enum):
    """Extended appointment tracking statuses"""
    SCHEDULED = "SCHEDULED"
    CONFIRMED = "CONFIRMED"
    REMINDED = "REMINDED"
    CHECKED_IN = "CHECKED_IN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    RESCHEDULED = "RESCHEDULED"


class NotificationPriority(str, Enum):
    """Notification priority levels"""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class NotificationPreference(BaseModel):
    """Doctor's notification preferences"""
    channel: NotificationChannel
    enabled: bool
    timing_minutes_before: int = Field(30, ge=0, le=1440)  # 0 to 24 hours
    notification_types: List[NotificationType]


class AppointmentTrackingInfo(BaseModel):
    """Comprehensive appointment tracking information"""
    appointment_ref: str
    patient_ref: str
    patient_name: str
    patient_phone: str
    patient_email: str
    appointment_date: str
    appointment_time: str
    duration_minutes: int
    tracking_status: AppointmentTrackingStatus
    appointment_status: str
    chief_complaint: Optional[str]
    department: str
    doctor_name: str
    
    # Tracking details
    scheduled_at: str
    confirmed_at: Optional[str]
    reminder_sent_at: Optional[str]
    checked_in_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    cancelled_at: Optional[str]
    
    # Notifications
    notifications_sent: List[Dict[str, Any]]
    next_notification: Optional[Dict[str, Any]]
    
    # Patient communication
    patient_confirmed: bool
    patient_reminder_count: int
    last_patient_contact: Optional[str]
    
    # Delays and updates
    estimated_delay_minutes: int
    delay_reason: Optional[str]
    updated_appointment_time: Optional[str]


class NotificationRequest(BaseModel):
    """Request to send notification"""
    appointment_ref: str
    notification_type: NotificationType
    channels: List[NotificationChannel]
    message: Optional[str] = None
    priority: NotificationPriority = NotificationPriority.NORMAL
    schedule_for: Optional[str] = None  # ISO datetime string
    custom_data: Optional[Dict[str, Any]] = None


class BulkNotificationRequest(BaseModel):
    """Request to send bulk notifications"""
    appointment_refs: List[str]
    notification_type: NotificationType
    channels: List[NotificationChannel]
    message_template: str
    priority: NotificationPriority = NotificationPriority.NORMAL
    schedule_for: Optional[str] = None


class AppointmentDelayUpdate(BaseModel):
    """Update appointment delay information"""
    appointment_ref: str
    delay_minutes: int = Field(..., ge=0, le=480)  # Max 8 hours delay
    reason: str
    notify_patient: bool = True
    estimated_new_time: Optional[str] = None


class PatientCommunicationLog(BaseModel):
    """Patient communication log entry"""
    communication_id: str
    appointment_ref: str
    patient_ref: str
    communication_type: str  # CALL, SMS, EMAIL, IN_PERSON
    direction: str  # INBOUND, OUTBOUND
    channel: str
    subject: Optional[str]
    message: str
    status: str  # SENT, DELIVERED, READ, FAILED
    sent_at: str
    delivered_at: Optional[str]
    read_at: Optional[str]
    response_received: bool
    response_message: Optional[str]
    created_by: str


class AppointmentReminderSettings(BaseModel):
    """Appointment reminder configuration"""
    enabled: bool = True
    reminder_intervals: List[int] = Field(default=[1440, 60, 15])  # 24h, 1h, 15min before
    channels: List[NotificationChannel] = Field(default=[NotificationChannel.SMS, NotificationChannel.EMAIL])
    custom_message_template: Optional[str] = None
    auto_confirm_required: bool = True
    max_reminder_attempts: int = Field(3, ge=1, le=10)


class AppointmentMetrics(BaseModel):
    """Appointment tracking metrics"""
    total_appointments: int
    confirmed_appointments: int
    checked_in_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    average_delay_minutes: float
    confirmation_rate: float
    show_up_rate: float
    on_time_rate: float
    patient_satisfaction_score: Optional[float]


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_user_context(current_user: User) -> dict:
    """Extract user context from JWT token"""
    user_roles = [role.name for role in current_user.roles]
    primary_role = UserRole.DOCTOR.value if UserRole.DOCTOR.value in user_roles else (user_roles[0] if user_roles else None)
    
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "role": primary_role,
        "all_roles": user_roles
    }


async def get_doctor_profile(user_context: dict, db: AsyncSession):
    """Get doctor profile with department information"""
    if UserRole.DOCTOR.value not in user_context.get("all_roles", []):
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
    if UserRole.DOCTOR.value not in user_context.get("all_roles", []):
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


def generate_communication_id() -> str:
    """Generate unique communication ID"""
    import random
    import string
    
    # Format: COMM-YYYYMMDD-XXXXXX
    date_str = datetime.now().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.digits, k=6))
    return f"COMM-{date_str}-{random_part}"


def determine_tracking_status(appointment: Appointment) -> AppointmentTrackingStatus:
    """Determine tracking status based on appointment data"""
    if appointment.cancelled_at:
        return AppointmentTrackingStatus.CANCELLED
    elif appointment.completed_at:
        return AppointmentTrackingStatus.COMPLETED
    elif appointment.checked_in_at:
        if appointment.status == "IN_PROGRESS":
            return AppointmentTrackingStatus.IN_PROGRESS
        else:
            return AppointmentTrackingStatus.CHECKED_IN
    elif appointment.status == "CONFIRMED":
        return AppointmentTrackingStatus.CONFIRMED
    elif appointment.status == AppointmentStatus.REQUESTED:
        return AppointmentTrackingStatus.SCHEDULED
    else:
        return AppointmentTrackingStatus.SCHEDULED


async def send_notification_async(
    notification_type: NotificationType,
    channels: List[NotificationChannel],
    recipient: dict,
    message: str,
    appointment_data: dict,
    priority: NotificationPriority = NotificationPriority.NORMAL
) -> Dict[str, Any]:
    """
    Async function to send notifications (mock implementation)
    In production, this would integrate with actual notification services
    """
    
    # Mock notification sending
    notification_results = []
    
    for channel in channels:
        result = {
            "channel": channel,
            "status": "SENT",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "message_id": f"MSG-{datetime.now().strftime('%Y%m%d%H%M%S')}-{channel}",
            "recipient": recipient.get("phone" if channel == NotificationChannel.SMS else "email"),
            "delivery_status": "PENDING"
        }
        
        # Simulate different delivery scenarios
        if channel == NotificationChannel.SMS:
            result["delivery_status"] = "DELIVERED"
            result["delivered_at"] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        elif channel == NotificationChannel.EMAIL:
            result["delivery_status"] = "DELIVERED"
            result["delivered_at"] = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        elif channel == NotificationChannel.PUSH:
            result["delivery_status"] = "DELIVERED"
            result["delivered_at"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        
        notification_results.append(result)
    
    return {
        "notification_id": f"NOTIF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "notification_type": notification_type,
        "priority": priority,
        "appointment_ref": appointment_data.get("appointment_ref"),
        "patient_ref": appointment_data.get("patient_ref"),
        "message": message,
        "channels": notification_results,
        "created_at": datetime.now(timezone.utc).isoformat()
    }


# ============================================================================
# APPOINTMENT TRACKING ENDPOINTS
# ============================================================================

@router.get("/appointments/today")
async def get_todays_appointment_tracking(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get today's appointments with comprehensive tracking information.
    
    Access Control:
    - Only Doctors can access their appointment tracking
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    today = date.today().isoformat()
    
    # Get today's appointments with patient details (doctor + hospital isolation)
    conditions = [
        Appointment.doctor_id == doctor.user_id,
        Appointment.appointment_date == today
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.department)
        )
        .order_by(asc(Appointment.appointment_time))
    )
    
    appointments = appointments_result.scalars().all()
    
    # Build tracking information for each appointment
    tracking_info = []
    
    for appointment in appointments:
        # Mock notification data (in production, this would come from notification service)
        notifications_sent = [
            {
                "notification_id": f"NOTIF-{appointment.id}-001",
                "type": "APPOINTMENT_CONFIRMATION",
                "channel": "SMS",
                "sent_at": (appointment.created_at + timedelta(minutes=5)).isoformat(),
                "status": "DELIVERED"
            }
        ]
        
        # Determine next notification
        next_notification = None
        appointment_datetime = _parse_appointment_datetime(appointment.appointment_date, appointment.appointment_time)
        time_until_appointment = appointment_datetime - datetime.now()
        
        if time_until_appointment.total_seconds() > 3600:  # More than 1 hour
            next_notification = {
                "type": "APPOINTMENT_REMINDER",
                "scheduled_for": (appointment_datetime - timedelta(hours=1)).isoformat(),
                "channels": ["SMS", "EMAIL"]
            }
        elif time_until_appointment.total_seconds() > 900:  # More than 15 minutes
            next_notification = {
                "type": "APPOINTMENT_REMINDER",
                "scheduled_for": (appointment_datetime - timedelta(minutes=15)).isoformat(),
                "channels": ["SMS"]
            }
        
        tracking_status = determine_tracking_status(appointment)
        
        tracking_info.append(AppointmentTrackingInfo(
            appointment_ref=appointment.appointment_ref,
            patient_ref=appointment.patient.patient_id,
            patient_name=f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            patient_phone=appointment.patient.user.phone,
            patient_email=appointment.patient.user.email,
            appointment_date=appointment.appointment_date,
            appointment_time=appointment.appointment_time,
            duration_minutes=appointment.duration_minutes,
            tracking_status=tracking_status,
            appointment_status=appointment.status,
            chief_complaint=appointment.chief_complaint,
            department=appointment.department.name,
            doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            
            # Tracking timestamps
            scheduled_at=appointment.created_at.isoformat(),
            confirmed_at=appointment.created_at.isoformat() if appointment.status == AppointmentStatus.CONFIRMED else None,
            reminder_sent_at=(appointment.created_at + timedelta(hours=1)).isoformat() if tracking_status in [AppointmentTrackingStatus.REMINDED, AppointmentTrackingStatus.CHECKED_IN, AppointmentTrackingStatus.COMPLETED] else None,
            checked_in_at=appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
            started_at=None,  # Would be tracked separately
            completed_at=appointment.completed_at.isoformat() if appointment.completed_at else None,
            cancelled_at=appointment.cancelled_at.isoformat() if appointment.cancelled_at else None,
            
            # Notifications
            notifications_sent=notifications_sent,
            next_notification=next_notification,
            
            # Patient communication
            patient_confirmed=appointment.status == AppointmentStatus.CONFIRMED,
            patient_reminder_count=1 if tracking_status in [AppointmentTrackingStatus.REMINDED, AppointmentTrackingStatus.CHECKED_IN, AppointmentTrackingStatus.COMPLETED] else 0,
            last_patient_contact=(appointment.created_at + timedelta(minutes=5)).isoformat(),
            
            # Delays
            estimated_delay_minutes=0,  # Would be calculated based on current schedule
            delay_reason=None,
            updated_appointment_time=None
        ))
    
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "date": today,
        "total_appointments": len(tracking_info),
        "tracking_summary": {
            "scheduled": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.SCHEDULED]),
            "confirmed": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.CONFIRMED]),
            "checked_in": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.CHECKED_IN]),
            "in_progress": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.IN_PROGRESS]),
            "completed": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.COMPLETED]),
            "cancelled": len([t for t in tracking_info if t.tracking_status == AppointmentTrackingStatus.CANCELLED])
        },
        "appointments": tracking_info
    }


@router.get("/appointments/{appointment_ref}/tracking")
async def get_appointment_tracking_details(
    appointment_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get detailed tracking information for a specific appointment.
    
    Access Control:
    - Only Doctors can access their appointment tracking details
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get appointment (doctor + hospital isolation)
    conditions = [
        Appointment.appointment_ref == appointment_ref,
        Appointment.doctor_id == doctor.user_id
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointment_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.department)
        )
    )
    
    appointment = appointment_result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Mock detailed tracking data (in production, this would come from tracking service)
    detailed_notifications = [
        {
            "notification_id": f"NOTIF-{appointment.id}-001",
            "type": "APPOINTMENT_CONFIRMATION",
            "channel": "SMS",
            "message": f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} is confirmed for {appointment.appointment_date} at {appointment.appointment_time}",
            "sent_at": (appointment.created_at + timedelta(minutes=5)).isoformat(),
            "delivered_at": (appointment.created_at + timedelta(minutes=5, seconds=30)).isoformat(),
            "status": "DELIVERED",
            "read_at": (appointment.created_at + timedelta(minutes=10)).isoformat()
        },
        {
            "notification_id": f"NOTIF-{appointment.id}-002",
            "type": "APPOINTMENT_REMINDER",
            "channel": "EMAIL",
            "message": f"Reminder: You have an appointment tomorrow at {appointment.appointment_time}",
            "sent_at": (appointment.created_at + timedelta(hours=23)).isoformat(),
            "delivered_at": (appointment.created_at + timedelta(hours=23, seconds=15)).isoformat(),
            "status": "DELIVERED",
            "read_at": None
        }
    ]
    
    # Mock communication log
    communication_log = [
        {
            "communication_id": "COMM-20241209-001",
            "communication_type": "SMS",
            "direction": "OUTBOUND",
            "channel": "SMS",
            "subject": None,
            "message": "Appointment confirmation",
            "status": "DELIVERED",
            "sent_at": (appointment.created_at + timedelta(minutes=5)).isoformat(),
            "delivered_at": (appointment.created_at + timedelta(minutes=5, seconds=30)).isoformat(),
            "read_at": (appointment.created_at + timedelta(minutes=10)).isoformat(),
            "response_received": True,
            "response_message": "Thank you, confirmed!",
            "created_by": str(doctor.user_id)
        }
    ]
    
    tracking_status = determine_tracking_status(appointment)
    
    tracking_details = AppointmentTrackingInfo(
        appointment_ref=appointment.appointment_ref,
        patient_ref=appointment.patient.patient_id,
        patient_name=f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
        patient_phone=appointment.patient.user.phone,
        patient_email=appointment.patient.user.email,
        appointment_date=appointment.appointment_date,
        appointment_time=appointment.appointment_time,
        duration_minutes=appointment.duration_minutes,
        tracking_status=tracking_status,
        appointment_status=appointment.status,
        chief_complaint=appointment.chief_complaint,
        department=appointment.department.name,
        doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        
        # Tracking timestamps
        scheduled_at=appointment.created_at.isoformat(),
        confirmed_at=appointment.created_at.isoformat() if appointment.status == AppointmentStatus.CONFIRMED else None,
        reminder_sent_at=(appointment.created_at + timedelta(hours=1)).isoformat() if tracking_status in [AppointmentTrackingStatus.REMINDED, AppointmentTrackingStatus.CHECKED_IN, AppointmentTrackingStatus.COMPLETED] else None,
        checked_in_at=appointment.checked_in_at.isoformat() if appointment.checked_in_at else None,
        started_at=None,
        completed_at=appointment.completed_at.isoformat() if appointment.completed_at else None,
        cancelled_at=appointment.cancelled_at.isoformat() if appointment.cancelled_at else None,
        
        # Notifications
        notifications_sent=detailed_notifications,
        next_notification=None,
        
        # Patient communication
        patient_confirmed=appointment.status == AppointmentStatus.CONFIRMED,
        patient_reminder_count=len(detailed_notifications),
        last_patient_contact=(appointment.created_at + timedelta(minutes=5)).isoformat(),
        
        # Delays
        estimated_delay_minutes=0,
        delay_reason=None,
        updated_appointment_time=None
    )
    
    return {
        "appointment_tracking": tracking_details,
        "communication_log": communication_log,
        "notification_history": detailed_notifications
    }


@router.get("/appointments/upcoming")
async def get_upcoming_appointments_tracking(
    days_ahead: int = Query(7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get upcoming appointments with tracking information.
    
    Access Control:
    - Only Doctors can access their upcoming appointments tracking
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Calculate date range
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    end_date_str = end_date.isoformat()

    
    # Get upcoming appointments (doctor + hospital isolation)
    conditions = [
        Appointment.doctor_id == doctor.user_id,
        Appointment.appointment_date >= today.isoformat(),
        Appointment.appointment_date <= end_date.isoformat(),
        Appointment.status.in_([AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED, AppointmentStatus.CHECKED_IN]) 
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))

    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(
            selectinload(Appointment.patient).selectinload(PatientProfile.user),
            selectinload(Appointment.department)
        )
        .order_by(asc(Appointment.appointment_date), asc(Appointment.appointment_time))
    )
    
    appointments = appointments_result.scalars().all()
    
    # Group appointments by date
    appointments_by_date = {}
    
    for appointment in appointments:
        appointment_date = appointment.appointment_date
        
        if appointment_date not in appointments_by_date:
            appointments_by_date[appointment_date] = []
        
        tracking_status = determine_tracking_status(appointment)
        
        # Calculate notification schedule
        appointment_datetime = _parse_appointment_datetime(appointment.appointment_date, appointment.appointment_time)
        notifications_schedule = []
        
        # 24 hours before
        reminder_24h = appointment_datetime - timedelta(hours=24)
        if reminder_24h > datetime.now():
            notifications_schedule.append({
                "type": "APPOINTMENT_REMINDER",
                "scheduled_for": reminder_24h.isoformat(),
                "channels": ["EMAIL", "SMS"],
                "message": "Reminder: You have an appointment tomorrow"
            })
        
        # 1 hour before
        reminder_1h = appointment_datetime - timedelta(hours=1)
        if reminder_1h > datetime.now():
            notifications_schedule.append({
                "type": "APPOINTMENT_REMINDER",
                "scheduled_for": reminder_1h.isoformat(),
                "channels": ["SMS"],
                "message": "Reminder: Your appointment is in 1 hour"
            })
        
        appointments_by_date[appointment_date].append({
            "appointment_ref": appointment.appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "patient_phone": appointment.patient.user.phone,
            "appointment_time": appointment.appointment_time,
            "duration_minutes": appointment.duration_minutes,
            "tracking_status": tracking_status,
            "appointment_status": appointment.status,
            "chief_complaint": appointment.chief_complaint,
            "patient_confirmed": appointment.status == AppointmentStatus.CONFIRMED,
            "notifications_schedule": notifications_schedule,
            "requires_confirmation": appointment.status == AppointmentStatus.REQUESTED
        })
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "date_range": {
            "start": today.isoformat(),
            "end": end_date.isoformat()
        },
        "total_appointments": len(appointments),
        "appointments_by_date": appointments_by_date,
        "summary": {
            "total_days": days_ahead,
            "days_with_appointments": len(appointments_by_date),
            "pending_confirmations": len([a for a in appointments if a.status == AppointmentStatus.REQUESTED]),
            "confirmed_appointments": len([a for a in appointments if a.status == AppointmentStatus.CONFIRMED])
        }
    }

# ============================================================================
# NOTIFICATION MANAGEMENT
# ============================================================================

@router.post("/notifications/send")
async def send_appointment_notification(
    request: NotificationRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Send notification for specific appointment.
    
    Access Control:
    - Only Doctors can send notifications for their appointments
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get appointment (doctor + hospital isolation)
    conditions = [
        Appointment.appointment_ref == request.appointment_ref,
        Appointment.doctor_id == doctor.user_id
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointment_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
    )
    
    appointment = appointment_result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Prepare recipient information
    recipient = {
        "patient_ref": appointment.patient.patient_id,
        "name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
        "phone": appointment.patient.user.phone,
        "email": appointment.patient.user.email
    }
    
    # Prepare appointment data
    appointment_data = {
        "appointment_ref": appointment.appointment_ref,
        "patient_ref": appointment.patient.patient_id,
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time,
        "department": "General Medicine"  # Would come from appointment.department.name
    }
    
    # Generate message if not provided
    if not request.message:
        message_templates = {
            NotificationType.APPOINTMENT_REMINDER: f"Reminder: You have an appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} on {appointment.appointment_date} at {appointment.appointment_time}",
            NotificationType.APPOINTMENT_CONFIRMATION: f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} is confirmed for {appointment.appointment_date} at {appointment.appointment_time}",
            NotificationType.APPOINTMENT_CANCELLATION: f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} on {appointment.appointment_date} has been cancelled",
            NotificationType.APPOINTMENT_RESCHEDULE: f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} has been rescheduled",
            NotificationType.APPOINTMENT_DELAY: f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} is running late. We'll update you shortly",
            NotificationType.FOLLOW_UP_REMINDER: f"This is a reminder for your follow-up appointment with Dr. {doctor.user.first_name} {doctor.user.last_name}"
        }
        message = message_templates.get(request.notification_type, "Appointment notification")
    else:
        message = request.message
    
    # Send notification asynchronously
    if request.schedule_for:
        # Schedule for later (in production, this would use a task queue)
        scheduled_time = datetime.fromisoformat(request.schedule_for.replace('Z', '+00:00'))
        delay_seconds = (scheduled_time - datetime.now(timezone.utc)).total_seconds()
        
        if delay_seconds > 0:
            # In production, this would be handled by a task scheduler like Celery
            notification_result = await send_notification_async(
                request.notification_type,
                request.channels,
                recipient,
                message,
                appointment_data,
                request.priority
            )
            notification_result["scheduled_for"] = request.schedule_for
            notification_result["status"] = "SCHEDULED"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scheduled time must be in the future"
            )
    else:
        # Send immediately
        background_tasks.add_task(
            send_notification_async,
            request.notification_type,
            request.channels,
            recipient,
            message,
            appointment_data,
            request.priority
        )
        
        notification_result = {
            "notification_id": f"NOTIF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "status": "QUEUED",
            "message": "Notification queued for immediate delivery"
        }
    
    return {
        "message": "Notification sent successfully",
        "appointment_ref": request.appointment_ref,
        "notification_type": request.notification_type,
        "channels": request.channels,
        "recipient": recipient["name"],
        "notification_result": notification_result
    }


@router.post("/notifications/bulk-send")
async def send_bulk_notifications(
    request: BulkNotificationRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Send bulk notifications for multiple appointments.
    
    Access Control:
    - Only Doctors can send bulk notifications for their appointments
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get appointments (doctor + hospital isolation)
    conditions = [
        Appointment.appointment_ref.in_(request.appointment_refs),
        Appointment.doctor_id == doctor.user_id
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
    )
    
    appointments = appointments_result.scalars().all()
    
    if len(appointments) != len(request.appointment_refs):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Some appointments not found or not accessible"
        )
    
    # Send notifications for each appointment
    notification_results = []
    
    for appointment in appointments:
        # Prepare recipient and message
        recipient = {
            "patient_ref": appointment.patient.patient_id,
            "name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "phone": appointment.patient.user.phone,
            "email": appointment.patient.user.email
        }
        
        # Personalize message template
        personalized_message = request.message_template.format(
            patient_name=recipient["name"],
            doctor_name=f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            appointment_date=appointment.appointment_date,
            appointment_time=appointment.appointment_time,
            appointment_ref=appointment.appointment_ref
        )
        
        appointment_data = {
            "appointment_ref": appointment.appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
            "appointment_date": appointment.appointment_date,
            "appointment_time": appointment.appointment_time
        }
        
        # Queue notification
        background_tasks.add_task(
            send_notification_async,
            request.notification_type,
            request.channels,
            recipient,
            personalized_message,
            appointment_data,
            request.priority
        )
        
        notification_results.append({
            "appointment_ref": appointment.appointment_ref,
            "patient_name": recipient["name"],
            "status": "QUEUED"
        })
    
    return {
        "message": "Bulk notifications queued successfully",
        "total_notifications": len(notification_results),
        "notification_type": request.notification_type,
        "channels": request.channels,
        "results": notification_results
    }


@router.get("/notifications/history")
async def get_notification_history(
    appointment_ref: Optional[str] = Query(None),
    notification_type: Optional[NotificationType] = Query(None),
    date_from: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    date_to: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get notification history with filtering options.
    
    Access Control:
    - Only Doctors can access their notification history
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Build query conditions for appointments (doctor + hospital isolation)
    conditions = [Appointment.doctor_id == doctor.user_id]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    if appointment_ref:
        conditions.append(Appointment.appointment_ref == appointment_ref)
    if date_from:
        conditions.append(Appointment.appointment_date >= date_from)
    if date_to:
        conditions.append(Appointment.appointment_date <= date_to)
    
    # Get appointments matching criteria
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
        .order_by(desc(Appointment.created_at))
        .limit(limit)
    )
    
    appointments = appointments_result.scalars().all()
    
    # Mock notification history (in production, this would come from notification service)
    notification_history = []
    
    for appointment in appointments:
        # Generate mock notifications for each appointment
        base_notifications = [
            {
                "notification_id": f"NOTIF-{appointment.id}-001",
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "notification_type": "APPOINTMENT_CONFIRMATION",
                "channels": [
                    {
                        "channel": "SMS",
                        "status": "DELIVERED",
                        "sent_at": (appointment.created_at + timedelta(minutes=5)).isoformat(),
                        "delivered_at": (appointment.created_at + timedelta(minutes=5, seconds=30)).isoformat()
                    }
                ],
                "message": f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} is confirmed",
                "priority": "NORMAL",
                "created_at": (appointment.created_at + timedelta(minutes=5)).isoformat()
            }
        ]
        
        # Add reminder notification if appointment is in future
        appointment_datetime = _parse_appointment_datetime(appointment.appointment_date, appointment.appointment_time)
        if appointment_datetime > datetime.now():
            base_notifications.append({
                "notification_id": f"NOTIF-{appointment.id}-002",
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "notification_type": "APPOINTMENT_REMINDER",
                "channels": [
                    {
                        "channel": "EMAIL",
                        "status": "DELIVERED",
                        "sent_at": (appointment_datetime - timedelta(hours=24)).isoformat(),
                        "delivered_at": (appointment_datetime - timedelta(hours=24) + timedelta(seconds=15)).isoformat()
                    }
                ],
                "message": "Reminder: You have an appointment tomorrow",
                "priority": "NORMAL",
                "created_at": (appointment_datetime - timedelta(hours=24)).isoformat()
            })
        
        # Filter by notification type if specified
        if notification_type:
            base_notifications = [n for n in base_notifications if n["notification_type"] == notification_type]
        
        notification_history.extend(base_notifications)
    
    # Sort by creation date
    notification_history.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_notifications": len(notification_history),
        "filters": {
            "appointment_ref": appointment_ref,
            "notification_type": notification_type,
            "date_from": date_from,
            "date_to": date_to
        },
        "notifications": notification_history[:limit]
    }


# ============================================================================
# APPOINTMENT DELAY MANAGEMENT
# ============================================================================

@router.post("/appointments/{appointment_ref}/delay")
async def update_appointment_delay(
    appointment_ref: str,
    request: AppointmentDelayUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update appointment delay and notify patient.
    
    Access Control:
    - Only Doctors can update delays for their appointments
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Get appointment (doctor + hospital isolation)
    conditions = [
        Appointment.appointment_ref == appointment_ref,
        Appointment.doctor_id == doctor.user_id
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointment_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
    )
    
    appointment = appointment_result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Check if appointment is today
    if appointment.appointment_date != date.today().isoformat():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only update delays for today's appointments"
        )
    
    # Calculate new estimated time
    original_time = _parse_appointment_time(appointment.appointment_time)
    original_datetime = datetime.combine(date.today(), original_time)
    new_datetime = original_datetime + timedelta(minutes=request.delay_minutes)
    new_time = new_datetime.time().strftime("%H:%M:%S")
    
    # Update appointment with delay information (in production, this would be stored in a delays table)
    delay_info = {
        "delay_minutes": request.delay_minutes,
        "reason": request.reason,
        "estimated_new_time": request.estimated_new_time or new_time,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(doctor.user_id)
    }
    import json
    appointment.notes = f"[DELAY]{json.dumps(delay_info)}[/DELAY]"
    await db.commit()
    # Send notification to patient if requested
    if request.notify_patient:
        recipient = {
            "patient_ref": appointment.patient.patient_id,
            "name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
            "phone": appointment.patient.user.phone,
            "email": appointment.patient.user.email
        }
        
        delay_message = f"Your appointment with Dr. {doctor.user.first_name} {doctor.user.last_name} is running {request.delay_minutes} minutes late. "
        if request.estimated_new_time:
            delay_message += f"New estimated time: {request.estimated_new_time}. "
        delay_message += f"Reason: {request.reason}. We apologize for the inconvenience."
        
        appointment_data = {
            "appointment_ref": appointment.appointment_ref,
            "patient_ref": appointment.patient.patient_id,
            "delay_minutes": request.delay_minutes,
            "reason": request.reason
        }
        
        # Queue delay notification
        background_tasks.add_task(
            send_notification_async,
            NotificationType.APPOINTMENT_DELAY,
            [NotificationChannel.SMS],
            recipient,
            delay_message,
            appointment_data,
            NotificationPriority.HIGH
        )
    
    return {
        "message": "Appointment delay updated successfully",
        "appointment_ref": appointment_ref,
        "delay_info": delay_info,
        "patient_notified": request.notify_patient,
        "original_time": appointment.appointment_time,
        "estimated_new_time": delay_info["estimated_new_time"]
    }


@router.get("/appointments/delays/today")
async def get_todays_delays(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get today's appointment delays summary.
    
    Access Control:
    - Only Doctors can access their appointment delays
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    today = date.today().isoformat()
    
    # Get today's appointments (doctor + hospital isolation)
    conditions = [
        Appointment.doctor_id == doctor.user_id,
        Appointment.appointment_date == today
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
        .order_by(asc(Appointment.appointment_time))
    )
    
    appointments = appointments_result.scalars().all()
    
    # Mock delay data (in production, this would come from delays tracking)
    delays_summary = []
    total_delay_minutes = 0
    
    import json

    for appointment in appointments:
        notes = appointment.notes or ""
        if "[DELAY]" in notes:
            try:
                delay_json = notes.split("[DELAY]")[1].split("[/DELAY]")[0]
                delay_info = json.loads(delay_json)
                total_delay_minutes += delay_info.get("delay_minutes", 0)
                delays_summary.append({
                    "appointment_ref": appointment.appointment_ref,
                    "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                    "original_time": appointment.appointment_time,
                    "delay_minutes": delay_info["delay_minutes"],
                    "estimated_new_time": delay_info["estimated_new_time"],
                    "reason": delay_info["reason"],
                    "patient_notified": True,
                    "updated_at": delay_info["updated_at"]
                })
            except Exception:
                pass
    
    # Calculate running delay (cumulative effect)
    current_running_delay = 0
    if delays_summary:
        current_running_delay = sum(d["delay_minutes"] for d in delays_summary[-2:])  # Last 2 delays
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "date": today,
        "total_appointments": len(appointments),
        "delayed_appointments": len(delays_summary),
        "total_delay_minutes": total_delay_minutes,
        "average_delay_minutes": round(total_delay_minutes / len(delays_summary), 1) if delays_summary else 0,
        "current_running_delay": current_running_delay,
        "delays": delays_summary
    }


# ============================================================================
# COMMUNICATION LOG
# ============================================================================

@router.get("/communication/log")
async def get_communication_log(
    appointment_ref: Optional[str] = Query(None),
    patient_ref: Optional[str] = Query(None),
    communication_type: Optional[str] = Query(None, pattern="^(CALL|SMS|EMAIL|IN_PERSON)$"),
    date_from: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    date_to: Optional[str] = Query(None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get patient communication log with filtering options.
    
    Access Control:
    - Only Doctors can access their communication logs
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Build query conditions (doctor + hospital isolation)
    conditions = [Appointment.doctor_id == doctor.user_id]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    if appointment_ref:
        conditions.append(Appointment.appointment_ref == appointment_ref)
    if date_from:
        conditions.append(Appointment.appointment_date >= date_from)
    if date_to:
        conditions.append(Appointment.appointment_date <= date_to)
    
    # Get appointments matching criteria
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient).selectinload(PatientProfile.user))
        .order_by(desc(Appointment.created_at))
        .limit(limit)
    )
    
    appointments = appointments_result.scalars().all()
    
    # Mock communication log (in production, this would come from communication service)
    communication_log = []
    
    for appointment in appointments:
        # Filter by patient_ref if specified
        if patient_ref and appointment.patient.patient_id != patient_ref:
            continue
        
        # Generate mock communication entries
        base_communications = [
            {
                "communication_id": f"COMM-{appointment.id}-001",
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "communication_type": "SMS",
                "direction": "OUTBOUND",
                "channel": "SMS",
                "subject": None,
                "message": "Appointment confirmation sent",
                "status": "DELIVERED",
                "sent_at": (appointment.created_at + timedelta(minutes=5)).isoformat(),
                "delivered_at": (appointment.created_at + timedelta(minutes=5, seconds=30)).isoformat(),
                "read_at": (appointment.created_at + timedelta(minutes=10)).isoformat(),
                "response_received": True,
                "response_message": "Thank you, confirmed!",
                "created_by": str(doctor.user_id)
            },
            {
                "communication_id": f"COMM-{appointment.id}-002",
                "appointment_ref": appointment.appointment_ref,
                "patient_ref": appointment.patient.patient_id,
                "patient_name": f"{appointment.patient.user.first_name} {appointment.patient.user.last_name}",
                "communication_type": "EMAIL",
                "direction": "OUTBOUND",
                "channel": "EMAIL",
                "subject": "Appointment Reminder",
                "message": "This is a reminder for your upcoming appointment",
                "status": "DELIVERED",
                "sent_at": (appointment.created_at + timedelta(hours=23)).isoformat(),
                "delivered_at": (appointment.created_at + timedelta(hours=23, seconds=15)).isoformat(),
                "read_at": None,
                "response_received": False,
                "response_message": None,
                "created_by": str(doctor.user_id)
            }
        ]
        
        # Filter by communication type if specified
        if communication_type:
            base_communications = [c for c in base_communications if c["communication_type"] == communication_type]
        
        communication_log.extend(base_communications)
    
    # Sort by sent_at date
    communication_log.sort(key=lambda x: x["sent_at"], reverse=True)
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "total_communications": len(communication_log),
        "filters": {
            "appointment_ref": appointment_ref,
            "patient_ref": patient_ref,
            "communication_type": communication_type,
            "date_from": date_from,
            "date_to": date_to
        },
        "communications": communication_log[:limit]
    }


@router.post("/communication/log")
async def create_communication_log_entry(
    communication_data: Dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new communication log entry.
    
    Access Control:
    - Only Doctors can create communication log entries
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Validate required fields
    required_fields = ["appointment_ref", "communication_type", "direction", "channel", "message"]
    for field in required_fields:
        if field not in communication_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required field: {field}"
            )
    
    # Get appointment to validate access (doctor + hospital isolation)
    conditions = [
        Appointment.appointment_ref == communication_data["appointment_ref"],
        Appointment.doctor_id == doctor.user_id
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointment_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
        .options(selectinload(Appointment.patient))
    )
    
    appointment = appointment_result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )
    
    # Create communication log entry
    communication_entry = PatientCommunicationLog(
        communication_id=generate_communication_id(),
        appointment_ref=communication_data["appointment_ref"],
        patient_ref=appointment.patient.patient_id,
        communication_type=communication_data["communication_type"],
        direction=communication_data["direction"],
        channel=communication_data["channel"],
        subject=communication_data.get("subject"),
        message=communication_data["message"],
        status=communication_data.get("status", "SENT"),
        sent_at=datetime.now(timezone.utc).isoformat(),
        delivered_at=communication_data.get("delivered_at"),
        read_at=communication_data.get("read_at"),
        response_received=communication_data.get("response_received", False),
        response_message=communication_data.get("response_message"),
        created_by=str(doctor.user_id)
    )
    
    # In production, this would be saved to a communication log database
    
    return {
        "message": "Communication log entry created successfully",
        "communication_entry": communication_entry.dict()
    }


# ============================================================================
# APPOINTMENT METRICS AND ANALYTICS
# ============================================================================

@router.get("/metrics/summary")
async def get_appointment_metrics_summary(
    period: str = Query("month", pattern="^(week|month|quarter|year)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get comprehensive appointment tracking metrics.
    
    Access Control:
    - Only Doctors can access their appointment metrics
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
    
    # Get appointments in period (doctor + hospital isolation)
    conditions = [
        Appointment.doctor_id == doctor.user_id,
        Appointment.appointment_date >= start_date.isoformat(),
        Appointment.appointment_date <= end_date.isoformat()
    ]
    if user_context.get("hospital_id"):
        conditions.append(Appointment.hospital_id == uuid.UUID(user_context["hospital_id"]))
    appointments_result = await db.execute(
        select(Appointment)
        .where(and_(*conditions))
    )
    
    appointments = appointments_result.scalars().all()
    
    # Calculate metrics
    total_appointments = len(appointments)
    confirmed_appointments = len([a for a in appointments if a.status == AppointmentStatus.CONFIRMED])
    checked_in_appointments = len([a for a in appointments if a.checked_in_at is not None])
    completed_appointments = len([a for a in appointments if a.status == AppointmentStatus.COMPLETED])
    cancelled_appointments = len([a for a in appointments if a.status == AppointmentStatus.CANCELLED])
    
    # Mock additional metrics (in production, these would come from tracking data)
    no_show_appointments = max(0, checked_in_appointments - completed_appointments - 2)  # Mock calculation
    average_delay_minutes = 12.5  # Mock average
    
    # Calculate rates
    confirmation_rate = round((confirmed_appointments / total_appointments * 100) if total_appointments > 0 else 0, 1)
    show_up_rate = round((checked_in_appointments / confirmed_appointments * 100) if confirmed_appointments > 0 else 0, 1)
    on_time_rate = round(((checked_in_appointments - no_show_appointments) / checked_in_appointments * 100) if checked_in_appointments > 0 else 0, 1)
    
    metrics = AppointmentMetrics(
        total_appointments=total_appointments,
        confirmed_appointments=confirmed_appointments,
        checked_in_appointments=checked_in_appointments,
        completed_appointments=completed_appointments,
        cancelled_appointments=cancelled_appointments,
        no_show_appointments=no_show_appointments,
        average_delay_minutes=average_delay_minutes,
        confirmation_rate=confirmation_rate,
        show_up_rate=show_up_rate,
        on_time_rate=on_time_rate,
        patient_satisfaction_score=4.2  # Mock score
    )
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "period": period,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "metrics": metrics,
        "trends": {
            "confirmation_trend": "+5.2%",  # Mock trend
            "show_up_trend": "+2.1%",
            "on_time_trend": "-1.3%",
            "satisfaction_trend": "+0.3"
        }
    }


@router.get("/settings/notifications")
async def get_notification_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get doctor's notification preferences.
    
    Access Control:
    - Only Doctors can access their notification settings
    """
    user_context = get_user_context(current_user)
    ensure_doctor_access(user_context)
    
    # Get doctor profile
    doctor = await get_doctor_profile(user_context, db)
    
    # Mock notification preferences (in production, this would come from user preferences)
    notification_preferences = [
        NotificationPreference(
            channel=NotificationChannel.SMS,
            enabled=True,
            timing_minutes_before=60,
            notification_types=[
                NotificationType.APPOINTMENT_REMINDER,
                NotificationType.APPOINTMENT_DELAY,
                NotificationType.APPOINTMENT_CANCELLATION
            ]
        ),
        NotificationPreference(
            channel=NotificationChannel.EMAIL,
            enabled=True,
            timing_minutes_before=1440,  # 24 hours
            notification_types=[
                NotificationType.APPOINTMENT_CONFIRMATION,
                NotificationType.APPOINTMENT_REMINDER,
                NotificationType.FOLLOW_UP_REMINDER
            ]
        ),
        NotificationPreference(
            channel=NotificationChannel.PUSH,
            enabled=False,
            timing_minutes_before=15,
            notification_types=[
                NotificationType.APPOINTMENT_CHECKIN
            ]
        )
    ]
    
    # Mock reminder settings
    reminder_settings = AppointmentReminderSettings(
        enabled=True,
        reminder_intervals=[1440, 60, 15],  # 24h, 1h, 15min
        channels=[NotificationChannel.SMS, NotificationChannel.EMAIL],
        custom_message_template="Hello {patient_name}, this is a reminder for your appointment with Dr. {doctor_name} on {appointment_date} at {appointment_time}",
        auto_confirm_required=True,
        max_reminder_attempts=3
    )
    
    return {
        "doctor_name": f"Dr. {doctor.user.first_name} {doctor.user.last_name}",
        "notification_preferences": notification_preferences,
        "reminder_settings": reminder_settings,
        "available_channels": [channel.value for channel in NotificationChannel],
        "available_notification_types": [ntype.value for ntype in NotificationType]
    }