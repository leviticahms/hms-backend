"""
Clinical Access Audit Trail Middleware.

FIX: Previously there was no record of WHO accessed which patient records,
WHEN, and from WHERE. This is a HIPAA compliance requirement.

This middleware logs every access to patient-facing clinical endpoints:
- Which user (user_id)
- Which patient record (extracted from URL)
- Which resource (endpoint path)
- What action (GET / POST / PUT / DELETE)
- IP address and User-Agent

Logs go to clinical_access_audit_log table (created in migration fix_001).
"""
import logging
import re
import uuid
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from jose import jwt, JWTError
from app.core.config import settings

logger = logging.getLogger(__name__)

# Patterns of clinical endpoints that require audit logging
AUDIT_PATTERNS = [
    re.compile(r"/patient-medical-history"),
    re.compile(r"/patient-discharge-summary"),
    re.compile(r"/doctor-patient-records"),
    re.compile(r"/lab/reports"),
    re.compile(r"/lab/results"),
    re.compile(r"/prescriptions"),
    re.compile(r"/patient-appointment-booking"),
    re.compile(r"/ipd-management"),
]


def _should_audit(path: str) -> bool:
    return any(p.search(path) for p in AUDIT_PATTERNS)


def _extract_patient_id_from_path(path: str) -> Optional[str]:
    """Best-effort extraction of patient_id or patient_ref from URL."""
    m = re.search(r"/patients?/([a-zA-Z0-9_-]+)", path)
    return m.group(1) if m else None


class ClinicalAuditMiddleware(BaseHTTPMiddleware):
    """
    Logs all accesses to clinical/patient endpoints for HIPAA compliance.
    Non-blocking — audit failures never block the actual request.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if not _should_audit(request.url.path):
            return response

        # Extract user context from JWT (best-effort, no exception on failure)
        user_id = None
        hospital_id = None
        try:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
                payload = jwt.decode(
                    token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
                )
                user_id_str = payload.get("sub") or payload.get("user_id")
                if user_id_str:
                    user_id = user_id_str
                hospital_id_str = payload.get("hospital_id")
                if hospital_id_str:
                    hospital_id = hospital_id_str
        except JWTError:
            pass
        except Exception:
            pass

        # Fire-and-forget audit log write
        try:
            await self._write_audit_log(
                request=request,
                user_id=user_id,
                hospital_id=hospital_id,
                response_status=response.status_code,
            )
        except Exception as e:
            logger.error(f"Clinical audit log write failed: {e}")

        return response

    async def _write_audit_log(
        self,
        request: Request,
        user_id: Optional[str],
        hospital_id: Optional[str],
        response_status: int,
    ):
        """Write audit record to clinical_access_audit_log table."""
        from app.database.session import AsyncSessionLocal
        from sqlalchemy import text

        patient_ref = _extract_patient_id_from_path(request.url.path)
        ip = request.client.host if request.client else None
        ua = request.headers.get("User-Agent", "")[:500]

        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO clinical_access_audit_log
                            (id, hospital_id, accessed_by, patient_id, resource, action,
                            ip_address, user_agent, accessed_at)
                        VALUES
                            (:id, :hospital_id, :accessed_by, :patient_id, :resource,
                            :action, :ip_address, :user_agent, NOW())
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "hospital_id": hospital_id,
                        "accessed_by": user_id,
                        "patient_id": patient_ref,
                        "resource": request.url.path[:100],
                        "action": request.method,
                        "ip_address": ip,
                        "user_agent": ua,
                    }
                )
                await db.commit()
        except Exception as e:
           logger.warning(f"Audit logging skipped: {e}")
        
        
