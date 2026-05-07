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

# One lab staff role + hospital admin (required for elevated actions / reads).
LAB_TECH_ROLE: Final[str] = "LAB_TECH"

LAB_ACCESS_ROLES: Final[List[str]] = [
    LAB_TECH_ROLE,
    "HOSPITAL_ADMIN",
]

LAB_GET_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)

LAB_MUTATION_ROLES: Final[List[str]] = list(LAB_ACCESS_ROLES)

LAB_EQUIPMENT_WRITE_ROLES: Final[List[str]] = list(LAB_MUTATION_ROLES)
