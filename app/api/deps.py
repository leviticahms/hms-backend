"""
Centralized API Dependencies
Provides unified authentication, RBAC, tenant scoping, and database session management.

This module centralizes all authentication and authorization logic to ensure consistency
across all API endpoints and eliminate code duplication.
"""
import uuid
from typing import List, Optional, Dict, Any, Callable
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import get_current_user
from app.models.user import User
from app.core.enums import UserRole


# ============================================================================
# CORE DEPENDENCIES
# ============================================================================

def get_db() -> AsyncSession:
    """
    Database session dependency.
    Alias for get_db_session for consistency.
    """
    return Depends(get_db_session)


def get_user() -> User:
    """
    Current authenticated user dependency.
    Alias for get_current_user for consistency.
    """
    return Depends(get_current_user)


# ============================================================================
# TENANT/HOSPITAL CONTEXT
# ============================================================================

def get_current_hospital_context(
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Extract hospital/tenant context from authenticated user.
    
    Returns:
        Dict containing:
        - user_id: Current user ID
        - hospital_id: Hospital ID (tenant isolation)
        - roles: List of user role names
        - permissions: List of user permissions (if available)
    
    Usage:
        @router.get("/endpoint")
        async def endpoint(context: Dict = Depends(get_current_hospital_context)):
            hospital_id = context["hospital_id"]
    """
    user_roles = [role.name for role in current_user.roles] if current_user.roles else []
    
    # Extract permissions from roles
    user_permissions = []
    if current_user.roles:
        for role in current_user.roles:
            if hasattr(role, 'permissions') and role.permissions:
                for permission in role.permissions:
                    user_permissions.append(permission.name)
    
    return {
        "user_id": str(current_user.id),
        "hospital_id": str(current_user.hospital_id) if current_user.hospital_id else None,
        "roles": user_roles,
        "permissions": list(set(user_permissions)),  # Remove duplicates
        "primary_role": user_roles[0] if user_roles else None
    }


def require_hospital_context(
    context: Dict[str, Any] = Depends(get_current_hospital_context)
) -> Dict[str, Any]:
    """
    Ensure user has hospital context (tenant isolation).
    Raises 403 if user doesn't belong to a hospital.
    
    Usage:
        @router.get("/hospital-scoped-endpoint")
        async def endpoint(context: Dict = Depends(require_hospital_context)):
            # Guaranteed to have hospital_id
    """
    if not context["hospital_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hospital context required. User must belong to a hospital."
        )
    
    return context


# ============================================================================
# ROLE-BASED ACCESS CONTROL (RBAC)
# ============================================================================

def require_roles(*required_roles: UserRole) -> Callable:
    """
    Dependency factory for role-based access control.
    
    Args:
        *required_roles: One or more UserRole enums required for access
    
    Returns:
        Dependency function that validates user has at least one required role
    
    Usage:
        @router.get("/doctor-only")
        async def endpoint(user: User = Depends(require_roles(UserRole.DOCTOR))):
            # Only doctors can access
        
        @router.get("/admin-or-doctor")
        async def endpoint(user: User = Depends(require_roles(UserRole.HOSPITAL_ADMIN, UserRole.DOCTOR))):
            # Hospital admins or doctors can access
    """
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        from app.core.role_aliases import normalize_staff_role_name

        raw = [getattr(role, "name", None) for role in (current_user.roles or [])]
        raw = [str(r).strip() for r in raw if r]
        user_roles_norm = {normalize_staff_role_name(r) for r in raw if r}

        # Convert required roles to strings for comparison
        required_role_names = [role.value for role in required_roles]

        if not any(req in user_roles_norm for req in required_role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(required_role_names)}",
            )

        return current_user

    return role_checker


def require_permissions(*required_permissions: str) -> Callable:
    """
    Dependency factory for permission-based access control.
    
    Args:
        *required_permissions: One or more permission strings required for access
    
    Returns:
        Dependency function that validates user has all required permissions
    
    Usage:
        @router.post("/create-user")
        async def endpoint(user: User = Depends(require_permissions("user.create"))):
            # Only users with user.create permission can access
    """
    def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        # Extract user permissions
        user_permissions = []
        if current_user.roles:
            for role in current_user.roles:
                if hasattr(role, 'permissions') and role.permissions:
                    for permission in role.permissions:
                        user_permissions.append(permission.name)
        
        # Check if user has all required permissions
        missing_permissions = set(required_permissions) - set(user_permissions)
        if missing_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Missing permissions: {', '.join(missing_permissions)}"
            )
        
        return current_user
    
    return permission_checker


# ============================================================================
# COMMON ROLE COMBINATIONS (CONVENIENCE DEPENDENCIES)
# ============================================================================

def require_super_admin() -> Callable:
    """Require Super Admin role"""
    return require_roles(UserRole.SUPER_ADMIN)


def require_hospital_admin() -> Callable:
    """Require Hospital Admin role"""
    return require_roles(UserRole.HOSPITAL_ADMIN)


def require_doctor() -> Callable:
    """Require Doctor role"""
    return require_roles(UserRole.DOCTOR)


def require_nurse() -> Callable:
    """Require Nurse role"""
    return require_roles(UserRole.NURSE)


def require_receptionist() -> Callable:
    """Require Receptionist role"""
    return require_roles(UserRole.RECEPTIONIST)


def require_receptionist_or_nurse() -> Callable:
    """Front-desk or ward staff who may manage doctor availability templates"""
    return require_roles(UserRole.RECEPTIONIST, UserRole.NURSE)


def require_patient() -> Callable:
    """Require Patient role"""
    return require_roles(UserRole.PATIENT)


def require_pharmacist() -> Callable:
    """Require Pharmacist role"""
    return require_roles(UserRole.PHARMACIST)


def require_lab_tech() -> Callable:
    """Require Lab Tech role"""
    return require_roles(UserRole.LAB_TECH)


def require_staff() -> Callable:
    """Require any staff role (non-patient, non-super-admin)"""
    return require_roles(
        UserRole.HOSPITAL_ADMIN,
        UserRole.DOCTOR,
        UserRole.NURSE,
        UserRole.RECEPTIONIST,
        UserRole.PHARMACIST,
        UserRole.LAB_TECH
    )


def require_admin_or_doctor() -> Callable:
    """Require Hospital Admin or Doctor role"""
    return require_roles(UserRole.HOSPITAL_ADMIN, UserRole.DOCTOR)


def require_admin_or_pharmacist() -> Callable:
    """Require Hospital Admin or Pharmacist role"""
    return require_roles(UserRole.HOSPITAL_ADMIN, UserRole.PHARMACIST)


def require_pharmacy_staff() -> Callable:
    """Require any pharmacy staff role"""
    return require_roles(
        UserRole.PHARMACIST,
        UserRole.HOSPITAL_ADMIN,
        UserRole.RECEPTIONIST
    )


def require_clinical_staff() -> Callable:
    """Require any clinical staff role"""
    return require_roles(
        UserRole.DOCTOR,
        UserRole.NURSE,
        UserRole.HOSPITAL_ADMIN
    )


def require_lab_staff() -> Callable:
    """Require any lab staff role"""
    return require_roles(
        UserRole.LAB_TECH,
        UserRole.DOCTOR,
        UserRole.HOSPITAL_ADMIN
    )


# ============================================================================
# COMBINED DEPENDENCIES (RBAC + TENANT SCOPING)
# ============================================================================

def require_hospital_admin_context() -> Callable:
    """
    Require Hospital Admin role with hospital context.
    Combines role validation and tenant scoping.
    """
    def dependency(
        user: User = Depends(require_hospital_admin()),
        context: Dict[str, Any] = Depends(require_hospital_context)
    ) -> Dict[str, Any]:
        return context
    
    return dependency


def require_doctor_context() -> Callable:
    """
    Require Doctor role with hospital context.
    Combines role validation and tenant scoping.
    """
    def dependency(
        user: User = Depends(require_doctor()),
        context: Dict[str, Any] = Depends(require_hospital_context)
    ) -> Dict[str, Any]:
        return context
    
    return dependency


def require_pharmacist_context() -> Callable:
    """
    Require Pharmacist role with hospital context.
    Combines role validation and tenant scoping.
    """
    def dependency(
        user: User = Depends(require_pharmacist()),
        context: Dict[str, Any] = Depends(require_hospital_context)
    ) -> Dict[str, Any]:
        return context
    
    return dependency


def require_clinical_staff_context() -> Callable:
    """
    Require clinical staff role with hospital context.
    Combines role validation and tenant scoping.
    """
    def dependency(
        user: User = Depends(require_clinical_staff()),
        context: Dict[str, Any] = Depends(require_hospital_context)
    ) -> Dict[str, Any]:
        return context
    
    return dependency


# ============================================================================
# SERVICE LAYER INTEGRATION
# ============================================================================

def get_service_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    """
    Get complete service context for business logic layer.
    
    Returns:
        Dict containing user, database session, and context information
        for passing to service layer methods.
    
    Usage:
        @router.get("/endpoint")
        async def endpoint(service_ctx: Dict = Depends(get_service_context)):
            service = SomeService(service_ctx["db"])
            result = await service.method(service_ctx["user"], service_ctx["context"])
    """
    context = get_current_hospital_context(current_user)
    
    return {
        "user": current_user,
        "db": db,
        "context": context,
        "user_id": context["user_id"],
        "hospital_id": context["hospital_id"],
        "roles": context["roles"],
        "permissions": context["permissions"]
    }


# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def validate_hospital_access(user_hospital_id: Optional[str], resource_hospital_id: str) -> None:
    """
    Validate user has access to hospital-scoped resource.
    
    Args:
        user_hospital_id: User's hospital ID
        resource_hospital_id: Resource's hospital ID
    
    Raises:
        HTTPException: If user doesn't have access to the resource
    """
    if not user_hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hospital context required"
        )
    
    if user_hospital_id != resource_hospital_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - Hospital isolation violation"
        )


def validate_user_roles(user_roles: List[str], required_roles: List[str]) -> None:
    """
    Validate user has required roles.
    
    Args:
        user_roles: User's current roles
        required_roles: Required roles for access
    
    Raises:
        HTTPException: If user doesn't have required roles
    """
    if not any(role in user_roles for role in required_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. Required roles: {', '.join(required_roles)}"
        )