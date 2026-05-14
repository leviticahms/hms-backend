"""
Security utilities for authentication and authorization.
Handles JWT tokens, password hashing, and permission checking.
"""

import uuid
import logging

from datetime import datetime, timedelta
from typing import Any, List, Optional

from jose import JWTError, jwt

from fastapi import (
    HTTPException,
    status,
    Depends,
)

from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
)

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from passlib.context import CryptContext

from app.core.config import settings
from app.core.database import get_platform_db_session

from app.models.user import (
    User,
    Role,
    Permission,
)

from app.core.enums import UserStatus


logger = logging.getLogger(__name__)

# USE ONLY ONE HASHING ALGORITHM
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

# JWT token scheme
security = HTTPBearer()


class SecurityManager:
    """Handles authentication and authorization"""

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash password using bcrypt.
        """

        return pwd_context.hash(password)

    @staticmethod
    def verify_password(
        plain_password: str,
        hashed_password: str
    ) -> bool:
        """
        Verify password against bcrypt hash.
        """

        if not hashed_password:
            return False

        try:

            return pwd_context.verify(
                plain_password,
                hashed_password
            )

        except Exception as e:

            logger.error(
                f"Password verification failed: {e}"
            )

            return False

    @staticmethod
    def generate_temp_password(
        length: int = 12
    ) -> str:
        """
        Generate temporary password.
        """

        import secrets
        import string

        lowercase = string.ascii_lowercase
        uppercase = string.ascii_uppercase
        digits = string.digits
        special = "!@#$%^&*"

        password = [
            secrets.choice(lowercase),
            secrets.choice(uppercase),
            secrets.choice(digits),
            secrets.choice(special)
        ]

        all_chars = (
            lowercase +
            uppercase +
            digits +
            special
        )

        for _ in range(length - 4):

            password.append(
                secrets.choice(all_chars)
            )

        secrets.SystemRandom().shuffle(password)

        return ''.join(password)

    @staticmethod
    def create_access_token(
        data: dict,
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """
        Create JWT access token.
        """

        to_encode = data.copy()

        if expires_delta:

            expire = datetime.utcnow() + expires_delta

        else:

            expire = (
                datetime.utcnow() +
                timedelta(
                    minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
                )
            )

        to_encode.update({
            "exp": expire,
            "type": "access"
        })

        return jwt.encode(
            to_encode,
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM
        )

    @staticmethod
    def create_refresh_token(
        data: dict
    ) -> str:
        """
        Create JWT refresh token.
        """

        to_encode = data.copy()

        expire = (
            datetime.utcnow() +
            timedelta(
                days=settings.REFRESH_TOKEN_EXPIRE_DAYS
            )
        )

        to_encode.update({
            "exp": expire,
            "type": "refresh"
        })

        return jwt.encode(
            to_encode,
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM
        )

    @staticmethod
    def verify_token(
        token: str,
        token_type: str = "access"
    ) -> dict:
        """
        Verify and decode JWT token.
        """

        try:

            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )

            # Validate token type
            if payload.get("type") != token_type:

                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type"
                )

            # Validate expiration
            exp = payload.get("exp")

            if exp and datetime.utcnow().timestamp() > exp:

                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired"
                )

            return payload

        except JWTError:

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )


def _jwt_sub_as_uuid(raw: Any) -> uuid.UUID:
    """
    Parse JWT sub safely as UUID.
    """

    if raw is None or str(raw).strip() == "":

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    try:

        return uuid.UUID(str(raw).strip())

    except ValueError:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user id in token",
        )


def _jwt_hospital_id_as_uuid(
    raw: Any
) -> Optional[uuid.UUID]:
    """
    Parse optional hospital UUID safely.
    """

    if raw is None:
        return None

    s = str(raw).strip()

    if s.lower() in (
        "",
        "none",
        "null",
        "undefined"
    ):
        return None

    try:

        return uuid.UUID(s)

    except ValueError:

        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_platform_db_session),
) -> User:
    """
    Get authenticated user from JWT token.
    """

    payload = SecurityManager.verify_token(
        credentials.credentials
    )

    user_uuid = _jwt_sub_as_uuid(
        payload.get("sub")
    )

    hospital_uuid = _jwt_hospital_id_as_uuid(
        payload.get("hospital_id")
    )

    # Multi-tenant fetch
    if hospital_uuid is not None:

        result = await db.execute(
            select(User)
            .where(
                User.id == user_uuid,
                User.hospital_id == hospital_uuid
            )
            .options(
                selectinload(User.roles)
                .selectinload(Role.permissions)
            )
        )

    else:

        result = await db.execute(
            select(User)
            .where(
                User.id == user_uuid
            )
            .options(
                selectinload(User.roles)
                .selectinload(Role.permissions)
            )
        )

    user = result.scalar_one_or_none()

    if not user:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    if user.status != UserStatus.ACTIVE:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is not active"
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get active authenticated user.
    """

    if current_user.status != UserStatus.ACTIVE:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    return current_user


def require_permissions(required_permissions: List[str]):
    """
    Require permissions decorator.
    """

    def permission_checker(
        current_user: User = Depends(get_current_user)
    ):

        user_permissions = []

        for role in current_user.roles:

            for permission in role.permissions:

                user_permissions.append(
                    permission.name
                )

        missing_permissions = (
            set(required_permissions) -
            set(user_permissions)
        )

        if missing_permissions:

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing_permissions)}"
            )

        return current_user

    return permission_checker


def require_roles(required_roles: List[Any]):
    """
    Require roles decorator.
    """

    from app.core.enums import UserRole as _UR

    normalized: List[str] = []

    for r in required_roles:

        if isinstance(r, _UR):
            normalized.append(r.value)
        else:
            normalized.append(str(r))

    def role_checker(
        current_user: User = Depends(get_current_user)
    ):

        from app.core.role_aliases import (
            normalize_staff_role_name
        )

        raw = [
            getattr(r, "name", None)
            for r in (current_user.roles or [])
        ]

        raw = [
            str(x).strip()
            for x in raw if x
        ]

        user_roles_norm = {
            normalize_staff_role_name(x)
            for x in raw if x
        }

        if not any(
            req in user_roles_norm
            for req in normalized
        ):

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required roles: {', '.join(normalized)}",
            )

        return current_user

    return role_checker


async def check_hospital_access(
    user: User,
    resource_hospital_id: int
) -> bool:
    """
    Enforce tenant isolation.
    """

    return (
        user.hospital_id ==
        resource_hospital_id
    )


def get_user_permissions(
    user: User
) -> List[str]:
    """
    Get user permissions.
    """

    permissions = []

    for role in user.roles:

        for permission in role.permissions:

            permissions.append(
                permission.name
            )

    return list(set(permissions))


def get_user_roles(
    user: User
) -> List[str]:
    """
    Get user roles.
    """

    return [
        role.name
        for role in user.roles
    ]