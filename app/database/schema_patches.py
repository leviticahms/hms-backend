"""
Idempotent DDL for schema drift between deployed databases and current models.

`create_all` does not add columns to existing tables; Alembic may not run if env
URLs or image revision sets differ from local. These patches keep critical
columns present without requiring manual SQL on every deploy.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import create_engine, inspect, text

from app.database.ssl_connect import psycopg2_engine_connect_args

logger = logging.getLogger(__name__)

_REQUIRED_ROLE_SPECS: list[dict[str, object]] = [
    {"name": "SUPER_ADMIN", "display_name": "Super Administrator", "description": "Platform Super Administrator", "level": 100},
    {"name": "HOSPITAL_ADMIN", "display_name": "Hospital Administrator", "description": "Hospital Administrator", "level": 90},
    {"name": "DOCTOR", "display_name": "Doctor", "description": "Medical Doctor", "level": 80},
    {"name": "PATHOLOGIST", "display_name": "Pathologist", "description": "Pathologist - signs off on lab results", "level": 78},
    {"name": "NURSE", "display_name": "Nurse", "description": "Registered Nurse", "level": 70},
    {"name": "PHARMACIST", "display_name": "Pharmacist", "description": "Licensed Pharmacist", "level": 65},
    {"name": "LAB_ADMIN", "display_name": "Lab Administrator", "description": "Laboratory Department Administrator", "level": 64},
    {"name": "LAB_SUPERVISOR", "display_name": "Lab Supervisor", "description": "Laboratory Supervisor - verifies and releases results", "level": 63},
    {"name": "LAB_TECH", "display_name": "Lab Technician", "description": "Laboratory Technician", "level": 62},
    {"name": "RECEPTIONIST", "display_name": "Receptionist", "description": "Front Desk Receptionist", "level": 60},
    {"name": "STAFF", "display_name": "Staff", "description": "General Staff Member", "level": 50},
    {"name": "PATIENT", "display_name": "Patient", "description": "Hospital Patient", "level": 10},
]


def _sync_url_from_env_async(async_url: str) -> str:
    value = (async_url or "").strip()
    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql://", 1)
    elif value.startswith("postgresql+asyncpg://"):
        value = value.replace("postgresql+asyncpg://", "postgresql://", 1)
    return value


def ensure_hospitals_tenant_database_name_column(sync_dsn: str) -> None:
    """Add hospitals.tenant_database_name + unique index if missing."""
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_hospitals_tenant_database_name_column: empty DSN, skipping")
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        insp = inspect(eng)
        if not insp.has_table("hospitals"):
            logger.debug("hospitals table missing; skipping tenant_database_name patch")
            return
        cols = {c["name"] for c in insp.get_columns("hospitals")}
        if "tenant_database_name" in cols:
            return
        logger.info("Applying patch: add hospitals.tenant_database_name (deploy / drift fix)")
        with eng.begin() as conn:
            conn.execute(
                text("ALTER TABLE hospitals ADD COLUMN tenant_database_name VARCHAR(63)")
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_hospitals_tenant_database_name "
                    "ON hospitals (tenant_database_name)"
                )
            )
    finally:
        eng.dispose()


def ensure_patient_profiles_opd_schema(sync_dsn: str) -> None:
    """
    Ensure patient_profiles has OPD registration columns and a hospital+patient_id index.

    Mirrors alembic `patient_profile_opd_fields_001` for deploys where migrations lag or
    DB_BOOTSTRAP_FROM_MODELS left older column sets.
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_patient_profiles_opd_schema: empty DSN, skipping")
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        insp = inspect(eng)
        if not insp.has_table("patient_profiles"):
            logger.debug("patient_profiles missing; skipping OPD schema patch")
            return

        col_names = {c["name"] for c in insp.get_columns("patient_profiles")}
        alters: list[str] = []
        if "id_type" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_type VARCHAR(50)")
        if "id_number" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_number VARCHAR(100)")
        if "id_name" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN id_name VARCHAR(255)")
        if "district" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN district VARCHAR(100)")
        if "medical_history" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN medical_history TEXT")
        if "blood_group_value" not in col_names:
            alters.append("ALTER TABLE patient_profiles ADD COLUMN blood_group_value VARCHAR(50)")

        with eng.begin() as conn:
            for stmt in alters:
                logger.info("Applying patch: %s", stmt[:80])
                conn.execute(text(stmt))

            row = conn.execute(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'patient_profiles'
                      AND column_name = 'blood_group'
                    """
                )
            ).fetchone()
            if row and row[0] is not None and row[0] < 20:
                logger.info("Applying patch: widen patient_profiles.blood_group to VARCHAR(20)")
                conn.execute(
                    text(
                        "ALTER TABLE patient_profiles "
                        "ALTER COLUMN blood_group TYPE VARCHAR(20)"
                    )
                )

            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_patient_profiles_hospital_patient_id "
                    "ON patient_profiles (hospital_id, patient_id)"
                )
            )
    finally:
        eng.dispose()

def ensure_staff_profiles_schema(sync_dsn: str) -> None:
    """Ensure staff_profiles has staff_name column."""
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_staff_profiles_schema: empty DSN, skipping")
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        insp = inspect(eng)
        if not insp.has_table("staff_profiles"):
            logger.debug("staff_profiles missing; skipping staff_name patch")
            return

        col_names = {c["name"] for c in insp.get_columns("staff_profiles")}
        alters: list[str] = []

        if "staff_name" not in col_names:
            alters.append(
                "ALTER TABLE staff_profiles ADD COLUMN staff_name VARCHAR(255)"
            )

        if not alters:
            return

        logger.info("Applying staff_profiles column patch (%d statement(s))", len(alters))
        with eng.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
    finally:
        eng.dispose()


def ensure_core_schema_drift_fixes_for_database(sync_dsn: str) -> None:
    """
    Apply all idempotent column patches for a single Postgres database (platform or tenant).

    Order: patient_profiles (OPD) first, then doctor_profiles (consultation fields).
    Safe to call on every process startup and lazily on first connection.
    """
    ensure_patient_profiles_opd_schema(sync_dsn)
    ensure_doctor_profiles_consultation_schema(sync_dsn)
    ensure_required_roles_catalog(sync_dsn)
    ensure_staff_profiles_schema(sync_dsn)


def ensure_required_roles_catalog(sync_dsn: str) -> None:
    """
    Ensure all canonical RBAC roles exist in this database.

    Tenant DBs are frequently created from partial schema/template snapshots where
    only a subset of roles may exist. This keeps `roles` complete and consistent.
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_required_roles_catalog: empty DSN, skipping")
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        insp = inspect(eng)
        if not insp.has_table("roles"):
            logger.debug("roles table missing; skipping role catalog seed")
            return

        with eng.begin() as conn:
            existing = {
                row[0]
                for row in conn.execute(text("SELECT name FROM roles")).fetchall()
                if row and row[0]
            }
            for role in _REQUIRED_ROLE_SPECS:
                name = str(role["name"])
                if name in existing:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO roles (id, name, display_name, description, is_system_role, level, is_active)
                        VALUES (:id, :name, :display_name, :description, :is_system_role, :level, :is_active)
                        ON CONFLICT (name) DO NOTHING
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "name": name,
                        "display_name": role["display_name"],
                        "description": role["description"],
                        "is_system_role": True,
                        "level": int(role["level"]),
                        "is_active": True,
                    },
                )
    finally:
        eng.dispose()


def ensure_doctor_profiles_consultation_schema(sync_dsn: str) -> None:
    """
    Ensure doctor_profiles has consultation_type + availability_time.

    Matches alembic `doctor_profile_consultation_fields_001` for deploys where migrations
    lag behind the SQLAlchemy model (avoids UndefinedColumnError on staff endpoints).
    """
    dsn = (sync_dsn or "").strip()
    if not dsn:
        logger.warning("ensure_doctor_profiles_consultation_schema: empty DSN, skipping")
        return

    eng = create_engine(dsn, connect_args=psycopg2_engine_connect_args())
    try:
        insp = inspect(eng)
        if not insp.has_table("doctor_profiles"):
            logger.debug("doctor_profiles missing; skipping consultation columns patch")
            return

        col_names = {c["name"] for c in insp.get_columns("doctor_profiles")}
        alters: list[str] = []
        if "consultation_type" not in col_names:
            alters.append(
                "ALTER TABLE doctor_profiles ADD COLUMN consultation_type VARCHAR(100)"
            )
        if "availability_time" not in col_names:
            alters.append(
                "ALTER TABLE doctor_profiles ADD COLUMN availability_time TEXT"
            )

        if not alters:
            return

        logger.info(
            "Applying doctor_profiles consultation/availability column patch (%d statement(s))",
            len(alters),
        )
        with eng.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
    finally:
        eng.dispose()
