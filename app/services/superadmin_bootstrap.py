"""
Ensure the platform superadmin account exists and matches SUPERADMIN_* env vars.
Used on app startup and before superadmin login attempts.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import UserRole, UserStatus
from app.core.security import SecurityManager
from app.models.user import Role, User, user_roles

logger = logging.getLogger(__name__)


async def _find_superadmin_user(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(func.lower(func.trim(User.email)) == email).limit(1)
    )
    user = result.scalar_one_or_none()
    if user:
        return user

    result = await db.execute(
        select(User)
        .join(user_roles, User.id == user_roles.c.user_id)
        .join(Role, user_roles.c.role_id == Role.id)
        .where(Role.name == UserRole.SUPER_ADMIN.value)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_superadmin_account(db: AsyncSession) -> bool:
    """
    Create or sync superadmin from env. Returns True when env credentials are configured.
    """
    email = (settings.SUPERADMIN_EMAIL or "").strip().lower()
    password = (settings.SUPERADMIN_PASSWORD or "").strip()
    if not email or not password:
        return False

    first = (settings.SUPERADMIN_FIRST_NAME or "").strip() or "Super"
    last = (settings.SUPERADMIN_LAST_NAME or "").strip() or "Admin"
    security = SecurityManager()

    role_result = await db.execute(
        select(Role).where(Role.name == UserRole.SUPER_ADMIN.value).limit(1)
    )
    role = role_result.scalar_one_or_none()
    if not role:
        role = Role(
            name=UserRole.SUPER_ADMIN.value,
            display_name="Super Administrator",
            description="Platform Super Administrator",
            level=100,
            is_system_role=True,
        )
        db.add(role)
        await db.flush()

    user = await _find_superadmin_user(db, email)
    changed = False

    if not user:
        user = User(
            hospital_id=None,
            email=email,
            phone=(settings.HOSPITAL_PHONE or "").strip() or "0000000000",
            password_hash=security.hash_password(password),
            first_name=first,
            last_name=last,
            status=UserStatus.ACTIVE,
            email_verified=True,
            phone_verified=False,
        )
        db.add(user)
        await db.flush()
        changed = True
        logger.info("Created superadmin user from environment: %s", email)
    else:
        if (user.email or "").strip().lower() != email:
            user.email = email
            changed = True
        if user.status != UserStatus.ACTIVE:
            user.status = UserStatus.ACTIVE
            changed = True
        if not user.email_verified:
            user.email_verified = True
            changed = True
        if user.first_name != first:
            user.first_name = first
            changed = True
        if user.last_name != last:
            user.last_name = last
            changed = True
        if not security.verify_password(password, user.password_hash):
            user.password_hash = security.hash_password(password)
            changed = True
            logger.info("Synced superadmin password from environment for %s", email)

    await db.execute(
        pg_insert(user_roles)
        .values(user_id=user.id, role_id=role.id)
        .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
    )

    if changed:
        await db.commit()
        logger.info("Superadmin account synced from environment")
    else:
        await db.commit()

    return True
