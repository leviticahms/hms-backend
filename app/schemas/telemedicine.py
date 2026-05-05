from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.enums import DeviceType, CallEventType

class CallTokenRequest(BaseModel):
    device_type: DeviceType = Field(..., description="Device type for the call")

class CallTokenResponse(BaseModel):
    token: str = Field(..., description="Video call token")
    expires_at: datetime = Field(..., description="Token expiration time")
    session_id: str = Field(..., description="Video session ID")

class JoinCallRequest(BaseModel):
    device_type: DeviceType = Field(..., description="Device type for the call")

class JoinCallResponse(BaseModel):
    participant_id: str = Field(..., description="Participant ID")
    session_id: str = Field(..., description="Video session ID")
    joined_at: datetime = Field(..., description="Join timestamp")

class CallControlRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = Field(None, description="Control payload")

class ReconnectRequest(BaseModel):
    device_type: DeviceType = Field(..., description="Device type for reconnection")

class CallParticipantResponse(BaseModel):
    participant_id: str
    user_id: str
    user_name: str
    role: str
    joined_at: datetime
    left_at: Optional[datetime] = None
    is_active: bool

class CallSummaryResponse(BaseModel):
    session_id: str
    duration_minutes: Optional[int] = None
    participants: List[CallParticipantResponse]
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str

class CallEventResponse(BaseModel):
    """Legacy call-event payload shape for older telemedicine clients."""
    id: Optional[str] = None
    event_type: str
    event_time: datetime
    user_id: Optional[str] = None
    participant_role: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    device_type: Optional[str] = None


class SessionReadinessResponse(BaseModel):
    ready: bool
    session_id: str
    message: str
    appointment_id: Optional[str] = None
    can_join_at: Optional[datetime] = None
