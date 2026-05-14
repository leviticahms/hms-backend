from __future__ import annotations

import asyncio
import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import insert as pg_insert

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.enums import UserRole, UserStatus
from app.core.security import SecurityManager
from app.database.session import AsyncSessionLocal
from app.models.user import Role, User, user_roles


async def main(fix: bool = False) -> None:
    email = (settings.SUPERADMIN_EMAIL or "").strip().lower()
    password = (settings.SUPERADMIN_PASSWORD or "").strip()
    sec = SecurityManager()

    print(f"ENV_EMAIL: {email}")
    print(f"ENV_PASSWORD_SET: {bool(password)}")

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(func.lower(User.email) == email)
        )
        user = res.scalar_one_or_none()
        if not user:
            res = await db.execute(
                select(User)
                .options(selectinload(User.roles))
                .join(user_roles, User.id == user_roles.c.user_id)
                .join(Role, user_roles.c.role_id == Role.id)
                .where(Role.name == UserRole.SUPER_ADMIN.value)
                .limit(1)
            )
            user = res.scalar_one_or_none()

        if not user:
            print("DB_USER_FOUND: False")
            if fix and email and password:
                role_res = await db.execute(select(Role).where(Role.name == UserRole.SUPER_ADMIN.value).limit(1))
                role = role_res.scalar_one_or_none()
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

                user = User(
                    hospital_id=None,
                    email=email,
                    phone=(settings.HOSPITAL_PHONE or "").strip() or "0000000000",
                    password_hash=sec.hash_password(password),
                    first_name=(settings.SUPERADMIN_FIRST_NAME or "").strip() or "Super",
                    last_name=(settings.SUPERADMIN_LAST_NAME or "").strip() or "Admin",
                    status=UserStatus.ACTIVE,
                    email_verified=True,
                    phone_verified=False,
                )
                db.add(user)
                await db.flush()
                await db.execute(
                    pg_insert(user_roles)
                    .values(user_id=user.id, role_id=role.id)
                    .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
                )
                await db.commit()
                print("FIX_APPLIED: Created superadmin from env")
            return

        if fix and email and password:
            role_res = await db.execute(select(Role).where(Role.name == UserRole.SUPER_ADMIN.value).limit(1))
            role = role_res.scalar_one_or_none()
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

            changed = False
            if (user.email or "").strip().lower() != email:
                user.email = email
                changed = True
            if user.status != UserStatus.ACTIVE:
                user.status = UserStatus.ACTIVE
                changed = True
            if not user.email_verified:
                user.email_verified = True
                changed = True
            if user.first_name != ((settings.SUPERADMIN_FIRST_NAME or "").strip() or "Super"):
                user.first_name = (settings.SUPERADMIN_FIRST_NAME or "").strip() or "Super"
                changed = True
            if user.last_name != ((settings.SUPERADMIN_LAST_NAME or "").strip() or "Admin"):
                user.last_name = (settings.SUPERADMIN_LAST_NAME or "").strip() or "Admin"
                changed = True
            if not sec.verify_password(password, user.password_hash):
                user.password_hash = sec.hash_password(password)
                changed = True
            await db.execute(
                pg_insert(user_roles)
                .values(user_id=user.id, role_id=role.id)
                .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
            )
            if changed:
                await db.commit()
                print("FIX_APPLIED: Synced superadmin from env")
            else:
                await db.commit()
                print("FIX_APPLIED: Role link ensured; no credential changes needed")

        roles = [r.name for r in (user.roles or [])]
        print("DB_USER_FOUND: True")
        print(f"DB_USER_ID: {user.id}")
        print(f"DB_USER_EMAIL: {user.email}")
        print(f"DB_USER_STATUS: {user.status}")
        print(f"DB_USER_ROLES: {roles}")
        print(f"DB_PASSWORD_HASH_SET: {bool(user.password_hash)}")
        print(f"PASSWORD_MATCH: {sec.verify_password(password, user.password_hash)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Create/sync superadmin from env")
    args = parser.parse_args()
    asyncio.run(main(fix=args.fix))
