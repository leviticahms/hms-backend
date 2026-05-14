"""
Role lists for /api/v1/lab/* routes.

Lab portal uses a single operational role (**LAB_TECH**) plus **HOSPITAL_ADMIN**
for oversight. LAB_ADMIN, LAB_SUPERVISOR, and PATHOLOGIST are not granted via
these lists — use ``Depends(require_roles(LAB_GET_ROLES))`` or
``Depends(require_roles(LAB_MUTATION_ROLES))`` so behavior stays consistent.

**RECEPTIONIST** is intentionally not included.

Use ``LAB_GET_ROLES`` on GET handlers and ``LAB_MUTATION_ROLES`` on POST/PUT/PATCH.
``LAB_EQUIPMENT_WRITE_ROLES`` is an alias for mutations (equipment tracking / QC equipment).
"""
from __future__ import annotations

from typing import Final, List

from app.core.enums import UserRole

# One lab staff role + hospital admin (oversight). LAB_ADMIN / LAB_SUPERVISOR are
# intentionally excluded from all /api/v1/lab/* route dependencies.
LAB_TECH_ROLE: Final[str] = UserRole.LAB_TECH.value

LAB_ACCESS_ROLES: Final[List[str]] = [
    UserRole.LAB_TECH.value,
    UserRole.HOSPITAL_ADMIN.value,
]

LAB_GET_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)

LAB_MUTATION_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)

LAB_EQUIPMENT_WRITE_ROLES: Final[List[str]] = list(LAB_MUTATION_ROLES)
