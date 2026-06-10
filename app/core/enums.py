"""
Core enums for the Hospital Management SaaS platform.
These enums are frozen for Phase 1 and must not change without formal review.
"""
from enum import Enum


class UserRole(str, Enum):
    """User roles in the system - supports RBAC"""
    SUPER_ADMIN = "SUPER_ADMIN"
    HOSPITAL_ADMIN = "HOSPITAL_ADMIN"
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    RECEPTIONIST = "RECEPTIONIST"
    PATIENT = "PATIENT"
    PHARMACIST = "PHARMACIST"
    LAB_TECH = "LAB_TECH"
    PATHOLOGIST = "PATHOLOGIST"


class UserStatus(str, Enum):
    """User account status"""
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class AppointmentStatus(str, Enum):
    """Appointment status for booking workflow"""
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AdmissionType(str, Enum):
    """Patient admission types"""
    OPD = "OPD"  # Outpatient Department
    IPD = "IPD"  # Inpatient Department


class PaymentStatus(str, Enum):
    """Payment transaction status"""
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class HospitalStatus(str, Enum):
    """Hospital operational status"""
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class SubscriptionStatus(str, Enum):
    """Hospital subscription status"""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


class SubscriptionPlan(str, Enum):
    """Available subscription plans"""
    FREE = "FREE"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"


class Gender(str, Enum):
    """Gender options"""
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class BloodGroup(str, Enum):
    """Blood group types"""
    A_POSITIVE = "A+"
    A_NEGATIVE = "A-"
    B_POSITIVE = "B+"
    B_NEGATIVE = "B-"
    AB_POSITIVE = "AB+"
    AB_NEGATIVE = "AB-"
    O_POSITIVE = "O+"
    O_NEGATIVE = "O-"


class DocumentType(str, Enum):
    """Patient document types"""
    MEDICAL_REPORT = "MEDICAL_REPORT"
    LAB_RESULT = "LAB_RESULT"
    PRESCRIPTION = "PRESCRIPTION"
    INSURANCE_CARD = "INSURANCE_CARD"
    ID_PROOF = "ID_PROOF"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"


class InvoiceStatus(str, Enum):
    """Invoice status"""
    DRAFT = "DRAFT"
    SENT = "SENT"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class DayOfWeek(str, Enum):
    """Days of the week for scheduling"""
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


class BedStatus(str, Enum):
    """Bed status for ward management"""
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    MAINTENANCE = "MAINTENANCE"
    RESERVED = "RESERVED"


class WardType(str, Enum):
    """Ward types for hospital organization"""
    ICU = "ICU"
    GENERAL = "GENERAL"
    EMERGENCY = "EMERGENCY"
    PRIVATE = "PRIVATE"
    MATERNITY = "MATERNITY"
    PEDIATRIC = "PEDIATRIC"
    SURGICAL = "SURGICAL"
    CARDIAC = "CARDIAC"


class AdmissionStatus(str, Enum):
    """Admission status for patient admissions"""
    PENDING = "PENDING"
    ADMITTED = "ADMITTED"
    DISCHARGED = "DISCHARGED"
    TRANSFERRED = "TRANSFERRED"
    CANCELLED = "CANCELLED"


class AuditAction(str, Enum):
    """Audit log actions for compliance"""
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    VIEW = "VIEW"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    EXPORT = "EXPORT"


class DosageForm(str, Enum):
    """Medicine dosage forms"""
    TABLET = "TABLET"
    CAPSULE = "CAPSULE"
    SYRUP = "SYRUP"
    INJECTION = "INJECTION"
    CREAM = "CREAM"
    OINTMENT = "OINTMENT"
    DROPS = "DROPS"
    INHALER = "INHALER"
    PATCH = "PATCH"
    POWDER = "POWDER"
    SUSPENSION = "SUSPENSION"
    LOTION = "LOTION"
    GEL = "GEL"


class MedicineCategory(str, Enum):
    """Medicine categories for classification"""
    ANTIBIOTIC = "ANTIBIOTIC"
    PAINKILLER = "PAINKILLER"
    VITAMIN = "VITAMIN"
    ANTACID = "ANTACID"
    ANTIHISTAMINE = "ANTIHISTAMINE"
    ANTIHYPERTENSIVE = "ANTIHYPERTENSIVE"
    ANTIDIABETIC = "ANTIDIABETIC"
    CARDIAC = "CARDIAC"
    RESPIRATORY = "RESPIRATORY"
    NEUROLOGICAL = "NEUROLOGICAL"
    DERMATOLOGICAL = "DERMATOLOGICAL"
    GASTROINTESTINAL = "GASTROINTESTINAL"
    HORMONAL = "HORMONAL"
    ANTIMALARIAL = "ANTIMALARIAL"
    ANTIVIRAL = "ANTIVIRAL"
    ANTIFUNGAL = "ANTIFUNGAL"
    SUPPLEMENT = "SUPPLEMENT"
    OTHER = "OTHER"


class MedicineStatus(str, Enum):
    """Medicine status for inventory management"""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class StockAdjustmentReason(str, Enum):
    """Stock adjustment reasons"""
    DAMAGED = "DAMAGED"
    EXPIRED = "EXPIRED"
    MANUAL_CORRECTION = "MANUAL_CORRECTION"
    STOCK_TAKE = "STOCK_TAKE"
    THEFT = "THEFT"
    RETURN = "RETURN"


# ============================================================================
# PHARMACY SALES ENUMS
# ============================================================================

class SaleType(str, Enum):
    """Sale types"""
    OTC = "OTC"  # Over-the-counter sale
    PRESCRIPTION = "PRESCRIPTION"  # Prescription dispense


class SaleStatus(str, Enum):
    """Sale status"""
    DRAFT = "DRAFT"  # Sale created but not paid
    PAID = "PAID"  # Payment completed
    COMPLETED = "COMPLETED"  # Sale completed and stock deducted
    CANCELLED = "CANCELLED"  # Sale cancelled


class PaymentMethod(str, Enum):
    """Payment methods"""
    CASH = "CASH"
    CARD = "CARD"
    UPI = "UPI"
    CREDIT = "CREDIT"  # For hospital staff/patients


class PaymentStatus(str, Enum):
    """Payment status"""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class ReturnReason(str, Enum):
    """Return reasons"""
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    WRONG_MEDICINE = "WRONG_MEDICINE"
    EXPIRED = "EXPIRED"
    DAMAGED = "DAMAGED"
    DOCTOR_CHANGE = "DOCTOR_CHANGE"


# ============================================================================
# SUPPLIER & PURCHASE ORDER ENUMS
# ============================================================================

class SupplierStatus(str, Enum):
    """Supplier status"""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


class PurchaseOrderStatus(str, Enum):
    """Purchase order status"""
    DRAFT = "DRAFT"  # Created but not submitted
    PENDING = "PENDING"  # Submitted, awaiting approval
    APPROVED = "APPROVED"  # Approved, ready to send to supplier
    SENT = "SENT"  # Sent to supplier
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"  # Some items received
    RECEIVED = "RECEIVED"  # All items received
    CANCELLED = "CANCELLED"  # Cancelled before completion


class PaymentTerms(str, Enum):
    """Payment terms with suppliers"""
    CASH_ON_DELIVERY = "CASH_ON_DELIVERY"
    NET_7 = "NET_7"  # Payment due in 7 days
    NET_15 = "NET_15"  # Payment due in 15 days
    NET_30 = "NET_30"  # Payment due in 30 days
    NET_45 = "NET_45"  # Payment due in 45 days
    NET_60 = "NET_60"  # Payment due in 60 days
    ADVANCE_PAYMENT = "ADVANCE_PAYMENT"  # Payment in advance


class StockTransactionType(str, Enum):
    """Stock transaction types"""
    RECEIPT = "RECEIPT"
    SALE = "SALE"
    ADJUSTMENT = "ADJUSTMENT"
    TRANSFER = "TRANSFER"


class ReceiptStatus(str, Enum):
    """Stock receipt status"""
    DRAFT = "DRAFT"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


# ============================================================================
# LAB TEST REGISTRATION ENUMS
# ============================================================================

class SampleType(str, Enum):
    """Lab test sample types"""
    BLOOD = "BLOOD"
    URINE = "URINE"
    SWAB = "SWAB"
    STOOL = "STOOL"
    SPUTUM = "SPUTUM"
    TISSUE = "TISSUE"
    FLUID = "FLUID"
    OTHER = "OTHER"


class LabOrderSource(str, Enum):
    """Lab order source types"""
    DOCTOR = "DOCTOR"
    WALKIN = "WALKIN"


class LabOrderPriority(str, Enum):
    """Lab order priority levels"""
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    STAT = "STAT"  # Immediate/Emergency


class LabOrderStatus(str, Enum):
    """Lab order status machine. Transitions validated in service."""
    DRAFT = "DRAFT"
    REGISTERED = "REGISTERED"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED"
    IN_PROCESS = "IN_PROCESS"
    IN_PROGRESS = "IN_PROGRESS"  # legacy DB value; use IN_PROCESS for new flow
    RESULT_ENTERED = "RESULT_ENTERED"
    APPROVED = "APPROVED"
    REPORTED = "REPORTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"  # legacy; prefer RESULT_ENTERED/APPROVED/REPORTED


class LabTestStatus(str, Enum):
    """Lab test status"""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class LabOrderItemStatus(str, Enum):
    """Lab order item status - aligned with order status where applicable."""
    DRAFT = "DRAFT"
    REGISTERED = "REGISTERED"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED"
    IN_PROCESS = "IN_PROCESS"
    RESULT_ENTERED = "RESULT_ENTERED"
    APPROVED = "APPROVED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"  # legacy


class SampleStatus(str, Enum):
    """Lab sample lifecycle. Transitions: REGISTERED→COLLECTED→RECEIVED→IN_PROCESS→STORED/DISCARDED; REJECTED from REGISTERED/COLLECTED."""
    REGISTERED = "REGISTERED"
    COLLECTED = "COLLECTED"
    RECEIVED = "RECEIVED"  # Received in lab; not yet in analysis
    IN_PROCESS = "IN_PROCESS"  # In analysis
    STORED = "STORED"
    DISCARDED = "DISCARDED"
    REJECTED = "REJECTED"


class ContainerType(str, Enum):
    """Sample container types"""
    EDTA = "EDTA"
    PLAIN = "PLAIN"
    FLUORIDE = "FLUORIDE"
    CITRATE = "CITRATE"
    STERILE_CUP = "STERILE_CUP"
    SWAB_TUBE = "SWAB_TUBE"
    BIOPSY_JAR = "BIOPSY_JAR"
    CULTURE_BOTTLE = "CULTURE_BOTTLE"


class RejectionReason(str, Enum):
    """Sample rejection reasons"""
    HEMOLYZED = "HEMOLYZED"
    INSUFFICIENT_VOLUME = "INSUFFICIENT_VOLUME"
    WRONG_LABEL = "WRONG_LABEL"
    LEAKED = "LEAKED"
    CONTAMINATED = "CONTAMINATED"
    EXPIRED_CONTAINER = "EXPIRED_CONTAINER"
    CLOTTED = "CLOTTED"
    OTHER = "OTHER"


class ResultStatus(str, Enum):
    """Lab result status"""
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"  # Pathologist approval; immutable after this
    RELEASED = "RELEASED"
    REJECTED = "REJECTED"


class ResultFlag(str, Enum):
    """Result value flags"""
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    LOW = "LOW"
    CRITICAL_HIGH = "CRITICAL_HIGH"
    CRITICAL_LOW = "CRITICAL_LOW"
    ABNORMAL = "ABNORMAL"


class CollectionSite(str, Enum):
    """Sample collection sites"""
    OPD_LAB = "OPD_LAB"
    IPD_WARD = "IPD_WARD"
    ICU = "ICU"
    EMERGENCY = "EMERGENCY"
    HOME_COLLECTION = "HOME_COLLECTION"
    CAMP = "CAMP"
    OTHER = "OTHER"


# ============================================================================
# EQUIPMENT & QC MANAGEMENT ENUMS
# ============================================================================

class EquipmentStatus(str, Enum):
    """Equipment operational status"""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    UNDER_MAINTENANCE = "UNDER_MAINTENANCE"
    DOWN = "DOWN"


class EquipmentCategory(str, Enum):
    """Equipment categories by lab section"""
    HEMATOLOGY = "HEMATOLOGY"
    BIOCHEMISTRY = "BIOCHEMISTRY"
    IMMUNOLOGY = "IMMUNOLOGY"
    MICROBIOLOGY = "MICROBIOLOGY"
    MOLECULAR = "MOLECULAR"
    HISTOPATHOLOGY = "HISTOPATHOLOGY"
    CYTOLOGY = "CYTOLOGY"
    GENERAL = "GENERAL"


class MaintenanceType(str, Enum):
    """Equipment maintenance types"""
    CALIBRATION = "CALIBRATION"
    PREVENTIVE = "PREVENTIVE"
    BREAKDOWN = "BREAKDOWN"
    REPAIR = "REPAIR"
    UPGRADE = "UPGRADE"
    CLEANING = "CLEANING"


class QCFrequency(str, Enum):
    """Quality control check frequency"""
    DAILY = "DAILY"
    SHIFT = "SHIFT"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class QCStatus(str, Enum):
    """Quality control run status"""
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"


class QCRuleStatus(str, Enum):
    """Quality control rule status"""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"

# ============================================================================
# REPORT SHARING & NOTIFICATION ENUMS
# ============================================================================

class ReportPublishStatus(str, Enum):
    """Lab report publish status"""
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    UNPUBLISHED = "UNPUBLISHED"


class ShareTokenStatus(str, Enum):
    """Share token status"""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ViewerType(str, Enum):
    """Allowed viewer types for shared reports"""
    PATIENT = "PATIENT"
    DOCTOR = "DOCTOR"
    PUBLIC = "PUBLIC"  # For secure links


class NotificationEventType(str, Enum):
    """Notification event types (unified HSM + lab)"""
    LAB_REPORT_READY = "LAB_REPORT_READY"
    LAB_REPORT_SHARED = "LAB_REPORT_SHARED"
    LAB_REPORT_UPDATED = "LAB_REPORT_UPDATED"
    APPOINTMENT_CONFIRM = "APPOINTMENT_CONFIRM"
    APPOINTMENT_REMINDER = "APPOINTMENT_REMINDER"
    PAYMENT_RECEIPT = "PAYMENT_RECEIPT"
    OTP = "OTP"
    BULK_SMS = "BULK_SMS"
    GENERAL = "GENERAL"


class NotificationStatus(str, Enum):
    """Notification delivery status"""
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NotificationChannel(str, Enum):
    """Notification delivery channels"""
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    IN_APP = "IN_APP"


class NotificationProviderType(str, Enum):
    """Provider type (email vs sms)"""
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationProviderName(str, Enum):
    """Supported provider names"""
    SENDGRID = "SENDGRID"
    AWS_SES = "AWS_SES"
    TWILIO = "TWILIO"
    MSG91 = "MSG91"
    AWS_SNS = "AWS_SNS"


class NotificationJobStatus(str, Enum):
    """Notification job (outbox) status"""
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NotificationDeliveryLogStatus(str, Enum):
    """Delivery log status per attempt"""
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    BOUNCED = "BOUNCED"
    FAILED = "FAILED"


# ============================================================================
# AUDIT TRAIL & COMPLIANCE ENUMS
# ============================================================================

class AuditEntityType(str, Enum):
    """Entity types for audit logging"""
    LAB_ORDER = "LAB_ORDER"
    SAMPLE = "SAMPLE"
    RESULT = "RESULT"
    REPORT = "REPORT"
    QC = "QC"
    EQUIPMENT = "EQUIPMENT"
    USER = "USER"


class AuditAction(str, Enum):
    """Audit actions for compliance tracking"""
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    VERIFY = "VERIFY"
    RELEASE = "RELEASE"
    REJECT = "REJECT"
    APPROVE = "APPROVE"
    CANCEL = "CANCEL"
    PUBLISH = "PUBLISH"
    UNPUBLISH = "UNPUBLISH"
    SHARE = "SHARE"
    REVOKE = "REVOKE"
    CALIBRATE = "CALIBRATE"
    MAINTAIN = "MAINTAIN"
    OVERRIDE = "OVERRIDE"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    EXPORT = "EXPORT"


class ExportFormat(str, Enum):
    """Export file formats"""
    CSV = "CSV"
    PDF = "PDF"
    EXCEL = "EXCEL"


class AnalyticsGroupBy(str, Enum):
    """Analytics grouping options"""
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    TEST = "TEST"
    SECTION = "SECTION"
    TECHNICIAN = "TECHNICIAN"


# ============================================================================
# TELEMEDICINE ENUMS
# ============================================================================

class AppointmentMode(str, Enum):
    """Appointment mode types"""
    OFFLINE = "OFFLINE"  # In-person appointment
    ONLINE = "ONLINE"   # Telemedicine appointment


class TeleAppointmentStatus(str, Enum):
    """Telemedicine appointment status"""
    SCHEDULED = "SCHEDULED"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class VideoProvider(str, Enum):
    """Video session providers"""
    WEBRTC = "WEBRTC"
    AGORA = "AGORA"
    TWILIO = "TWILIO"


class VideoSessionStatus(str, Enum):
    """Video session status"""
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    EXPIRED = "EXPIRED"


class DeviceType(str, Enum):
    """Device types for video calls"""
    WEB = "WEB"
    ANDROID = "ANDROID"
    IOS = "IOS"
    DESKTOP = "DESKTOP"


class CallEventType(str, Enum):
    """Call event types for tracking"""
    JOIN = "JOIN"
    LEAVE = "LEAVE"
    DROP = "DROP"
    REJOIN = "REJOIN"
    END = "END"
    MUTE = "MUTE"
    UNMUTE = "UNMUTE"
    VIDEO_OFF = "VIDEO_OFF"
    VIDEO_ON = "VIDEO_ON"
    CAMERA_SWITCH = "CAMERA_SWITCH"


class ConnectionState(str, Enum):
    """Participant connection state"""
    CONNECTED = "CONNECTED"
    DROPPED = "DROPPED"
    RECONNECTED = "RECONNECTED"
    DISCONNECTED = "DISCONNECTED"


class ParticipantRole(str, Enum):
    """Video call participant roles"""
    DOCTOR = "DOCTOR"
    PATIENT = "PATIENT"


# ============================================================================
# PRESCRIPTION MANAGEMENT ENUMS
# ============================================================================

class PrescriptionStatus(str, Enum):
    """Digital prescription status"""
    DRAFT = "DRAFT"
    SIGNED = "SIGNED"
    CANCELLED = "CANCELLED"


class IntegrationType(str, Enum):
    """Integration target types"""
    PHARMACY = "PHARMACY"
    LAB = "LAB"


class IntegrationStatus(str, Enum):
    """Integration status tracking"""
    PENDING = "PENDING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TestUrgency(str, Enum):
    """Lab test urgency levels"""
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    STAT = "STAT"


# ============================================================================
# SURGERY MODULE ENUMS
# ============================================================================

class SurgeryType(str, Enum):
    """Surgery type classification"""
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    EMERGENCY = "EMERGENCY"


class SurgeryCaseStatus(str, Enum):
    """Surgery case workflow status"""
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SurgeryTeamRole(str, Enum):
    """Role of a staff member in the surgical team"""
    LEAD_SURGEON = "LEAD_SURGEON"
    ASSISTANT = "ASSISTANT"
    ANESTHESIOLOGIST = "ANESTHESIOLOGIST"
    SUPPORTING = "SUPPORTING"