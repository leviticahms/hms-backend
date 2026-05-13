"""
Normalize persisted Role.name values so login and staff lists stay consistent.

Some UIs or legacy rows store labels like "Pharmacists" instead of enum value "PHARMACIST".
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set

from app.core.enums import UserRole

# Canonical staff role strings in stable display order (includes catalog roles not on UserRole enum).
STAFF_ROLE_ORDER: tuple[str, ...] = (
    UserRole.DOCTOR.value,
    UserRole.PATHOLOGIST.value,
    UserRole.NURSE.value,
    UserRole.RECEPTIONIST.value,
    UserRole.LAB_TECH.value,
    "LAB_ADMIN",
    "LAB_SUPERVISOR",
    UserRole.PHARMACIST.value,
    "STAFF",
)

# Map common misspellings / plural labels (any case input normalized to UPPER first) -> canonical.
_STAFF_ROLE_ALIASES_UPPER: Dict[str, str] = {
    "PHARMACISTS": UserRole.PHARMACIST.value,
    "PHARMSICT": UserRole.PHARMACIST.value,
    "PHARMIST": UserRole.PHARMACIST.value,
    "PHRAMACIST": UserRole.PHARMACIST.value,
    "PHARMACIST": UserRole.PHARMACIST.value,
    "DOCTORS": UserRole.DOCTOR.value,
    "DOCTOR": UserRole.DOCTOR.value,
    "PHYSICIAN": UserRole.DOCTOR.value,
    "CONSULTANT": UserRole.DOCTOR.value,
    "NURSES": UserRole.NURSE.value,
    "NURSE": UserRole.NURSE.value,
    "REGISTERED NURSE": UserRole.NURSE.value,
    "STAFF NURSE": UserRole.NURSE.value,
    "HEAD NURSE": UserRole.NURSE.value,
    "RECEPTIONISTS": UserRole.RECEPTIONIST.value,
    "RECEPTIONIST": UserRole.RECEPTIONIST.value,
    "RECEPTION": UserRole.RECEPTIONIST.value,
    "FRONT DESK": UserRole.RECEPTIONIST.value,
    "FRONT DESK RECEPTIONIST": UserRole.RECEPTIONIST.value,
    "SECRETARY": UserRole.RECEPTIONIST.value,
    "LAB_TECHS": UserRole.LAB_TECH.value,
    "LAB_TECH": UserRole.LAB_TECH.value,
    "LAB TECH": UserRole.LAB_TECH.value,
    "LAB TECHNICIAN": UserRole.LAB_TECH.value,
    "LABORATORY TECHNICIAN": UserRole.LAB_TECH.value,
    "LABORATORY TECH": UserRole.LAB_TECH.value,
    "MEDICAL LABORATORY TECHNICIAN": UserRole.LAB_TECH.value,
    "MLT": UserRole.LAB_TECH.value,
    "LABTECH": UserRole.LAB_TECH.value,
    "PATHOLOGIST": UserRole.PATHOLOGIST.value,
    "PATHOLOGISTS": UserRole.PATHOLOGIST.value,
    "LAB_ADMIN": "LAB_ADMIN",
    "LAB ADMIN": "LAB_ADMIN",
    "LAB-ADMIN": "LAB_ADMIN",
    "LABORATORY ADMINISTRATOR": "LAB_ADMIN",
    "LAB SUPERVISOR": "LAB_SUPERVISOR",
    "LAB_SUPERVISOR": "LAB_SUPERVISOR",
    "LAB-SUPERVISOR": "LAB_SUPERVISOR",
    "STAFF": "STAFF",
    "GENERAL STAFF": "STAFF",
}

# Extra literals that may appear in Role.name (mixed case) from older clients / seeds.
_STAFF_ROLE_EXTRA_SQL_LITERALS: frozenset[str] = frozenset(
    {
        "Pharmacists",
        "Pharmacist",
        "pharmacists",
        "Pharmsict",
        "pharmsict",
        "Doctor",
        "Doctors",
        "Nurse",
        "Nurses",
        "Receptionist",
        "Receptionists",
        "Lab Technician",
        "Lab Technicians",
        "Lab technician",
        "Pathologist",
        "Pathologists",
        "Lab Administrator",
        "Lab Supervisor",
    }
)


def normalize_staff_role_name(raw: Optional[str]) -> str:
    """Return canonical staff role token (e.g. PHARMACIST) or uppercased unknown string."""
    if raw is None:
        return ""
    u = str(raw).strip().upper()
    return _STAFF_ROLE_ALIASES_UPPER.get(u, u)


def staff_login_allowed_roles_normalized(raw_roles: List[str]) -> Set[str]:
    """Set of canonical role names for RBAC checks."""
    return {normalize_staff_role_name(r) for r in raw_roles if r}


def user_can_use_staff_login(raw_roles: List[str]) -> bool:
    allowed = {
        "SUPER_ADMIN",
        "HOSPITAL_ADMIN",
        UserRole.DOCTOR.value,
        UserRole.NURSE.value,
        UserRole.RECEPTIONIST.value,
        UserRole.PHARMACIST.value,
        UserRole.LAB_TECH.value,
        UserRole.PATHOLOGIST.value,
        "LAB_ADMIN",
        "LAB_SUPERVISOR",
        "STAFF",
    }
    return bool(staff_login_allowed_roles_normalized(raw_roles) & allowed)


def primary_staff_role_for_display(raw_roles: List[str]) -> Optional[str]:
    """Pick first matching canonical staff role from STAFF_ROLE_ORDER."""
    norm = staff_login_allowed_roles_normalized(raw_roles)
    for cand in STAFF_ROLE_ORDER:
        if cand in norm:
            return cand
    return None


def all_sql_role_names_for_staff_directory() -> tuple[str, ...]:
    """Every Role.name variant used in SQL ``IN (...)`` for staff directory queries."""
    out: List[str] = []
    seen: set[str] = set()
    for x in list(STAFF_ROLE_ORDER) + list(_STAFF_ROLE_ALIASES_UPPER.keys()) + list(_STAFF_ROLE_EXTRA_SQL_LITERALS):
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def role_name_variants_for_sql_filter(canonical_or_alias: str) -> tuple[str, ...]:
    """All ``Role.name`` literals that should match one staff role (for role_filter= queries)."""
    canon = normalize_staff_role_name(canonical_or_alias)
    if not canon:
        return tuple()
    out: set[str] = {canon}
    for alias, c in _STAFF_ROLE_ALIASES_UPPER.items():
        if c == canon:
            out.add(alias)
    for lit in _STAFF_ROLE_EXTRA_SQL_LITERALS:
        if normalize_staff_role_name(lit) == canon:
            out.add(lit)
    return tuple(out)
