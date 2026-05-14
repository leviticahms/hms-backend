"""
Check database connectivity and compare model metadata with live DB schema.

Usage:
    python scripts/check_db_integrity.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.models.base import Base


def _norm_type(t: sa.types.TypeEngine) -> str:
    return str(t).lower().replace(" ", "")


def main() -> int:
    try:
        engine = sa.create_engine(settings.DATABASE_URL_SYNC, pool_pre_ping=True)
        inspector = sa.inspect(engine)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print("DB_CONNECTIVITY: FAIL")
        print(f"ERROR: {exc}")
        return 2

    print("DB_CONNECTIVITY: OK")

    model_tables = set(Base.metadata.tables.keys())
    db_tables = set(inspector.get_table_names())

    missing_tables = sorted(model_tables - db_tables)
    extra_tables = sorted(db_tables - model_tables)

    print(f"MODEL_TABLES: {len(model_tables)}")
    print(f"DB_TABLES: {len(db_tables)}")
    print(f"MISSING_TABLES_IN_DB: {len(missing_tables)}")
    for t in missing_tables:
        print(f"  - {t}")
    print(f"EXTRA_TABLES_IN_DB: {len(extra_tables)}")
    for t in extra_tables:
        print(f"  - {t}")

    missing_columns: dict[str, list[str]] = defaultdict(list)
    extra_columns: dict[str, list[str]] = defaultdict(list)
    type_mismatches: dict[str, list[str]] = defaultdict(list)
    nullable_mismatches: dict[str, list[str]] = defaultdict(list)

    for table_name in sorted(model_tables & db_tables):
        model_table = Base.metadata.tables[table_name]
        model_cols = {c.name: c for c in model_table.columns}
        db_cols_raw = inspector.get_columns(table_name)
        db_cols = {c["name"]: c for c in db_cols_raw}

        for name in sorted(set(model_cols) - set(db_cols)):
            missing_columns[table_name].append(name)
        for name in sorted(set(db_cols) - set(model_cols)):
            extra_columns[table_name].append(name)

        for name in sorted(set(model_cols) & set(db_cols)):
            model_col = model_cols[name]
            db_col = db_cols[name]

            model_type = _norm_type(model_col.type)
            db_type = _norm_type(db_col["type"])
            if model_type != db_type:
                type_mismatches[table_name].append(
                    f"{name}: model={model_type}, db={db_type}"
                )

            model_nullable = bool(model_col.nullable)
            db_nullable = bool(db_col.get("nullable", True))
            if model_nullable != db_nullable:
                nullable_mismatches[table_name].append(
                    f"{name}: model_nullable={model_nullable}, db_nullable={db_nullable}"
                )

    print(f"MISSING_COLUMNS: {sum(len(v) for v in missing_columns.values())}")
    for table_name, cols in missing_columns.items():
        print(f"  - {table_name}: {', '.join(cols)}")

    print(f"EXTRA_COLUMNS: {sum(len(v) for v in extra_columns.values())}")
    for table_name, cols in extra_columns.items():
        print(f"  - {table_name}: {', '.join(cols)}")

    print(f"TYPE_MISMATCHES: {sum(len(v) for v in type_mismatches.values())}")
    for table_name, mismatches in type_mismatches.items():
        for mismatch in mismatches:
            print(f"  - {table_name}: {mismatch}")

    print(
        f"NULLABLE_MISMATCHES: {sum(len(v) for v in nullable_mismatches.values())}"
    )
    for table_name, mismatches in nullable_mismatches.items():
        for mismatch in mismatches:
            print(f"  - {table_name}: {mismatch}")

    has_issues = any(
        (
            missing_tables,
            missing_columns,
            type_mismatches,
            nullable_mismatches,
        )
    )
    if has_issues:
        print("RESULT: FAIL (schema differences detected)")
        return 1

    print("RESULT: PASS (tables/fields match model metadata)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
