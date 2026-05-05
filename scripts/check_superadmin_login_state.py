from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.security import SecurityManager
from app.database.session import AsyncSessionLocal
from app.models.user import User


async def main() -> None:
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
            print("DB_USER_FOUND: False")
            return

        roles = [r.name for r in (user.roles or [])]
        print("DB_USER_FOUND: True")
        print(f"DB_USER_ID: {user.id}")
        print(f"DB_USER_EMAIL: {user.email}")
        print(f"DB_USER_STATUS: {user.status}")
        print(f"DB_USER_ROLES: {roles}")
        print(f"DB_PASSWORD_HASH_SET: {bool(user.password_hash)}")
        print(f"PASSWORD_MATCH: {sec.verify_password(password, user.password_hash)}")


if __name__ == "__main__":
    asyncio.run(main())
