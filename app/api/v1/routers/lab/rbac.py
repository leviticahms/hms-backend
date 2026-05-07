"""
Role lists for /api/v1/lab/* routes.

Lab APIs are restricted to the operational lab role only (**LAB_TECH**).
LAB_ADMIN and LAB_SUPERVISOR are intentionally excluded.
"""
from __future__ import annotations

from typing import Final, List

from app.core.enums import UserRole

LAB_TECH_ROLE: Final[str] = UserRole.LAB_TECH.value

LAB_ACCESS_ROLES: Final[List[str]] = [
    LAB_TECH_ROLE,
]

LAB_GET_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)
LAB_MUTATION_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)
LAB_EQUIPMENT_WRITE_ROLES: Final[List[str]] = list(LAB_MUTATION_ROLES)
