"""
Application-facing DB facade (imports session factories from app.database.session).

Lower-level pieces live under app.database/: engines, routing, tenant_context, ssl helpers.
Alembic upgrades run here for convenience; pools are owned by app.database.session / engines.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging
from alembic.config import Config
from alembic import command
import os
import threading

from app.core.config import settings

# Single engine and session factory - imported from app.database.session
from app.database.session import (
    AsyncSessionLocal,
    close_database as close_database_pools,
    get_async_engine,
    get_db_session,
    get_platform_db_session,
    get_tenant_session_factory,
    invalidate_hospital_tenant_cache,
    resolve_tenant_database_name_for_hospital,
)

logger = logging.getLogger(__name__)

# Migration lock to prevent double-runs during uvicorn reload
_migration_lock = threading.Lock()
_migrations_completed = False


def run_alembic_upgrade():
    """Run Alembic upgrade to head synchronously with reload protection."""
    global _migrations_completed
    
    # Protect against uvicorn reload double-run
    with _migration_lock:
        if _migrations_completed:
            logger.info("Migrations already completed in this process")
            return
        
        try:
            # Get the directory containing alembic.ini
            alembic_cfg_path = os.path.join(os.getcwd(), "alembic.ini")
            
            if not os.path.exists(alembic_cfg_path):
                raise FileNotFoundError(f"alembic.ini not found at {alembic_cfg_path}")
            
            # Create Alembic config
            alembic_cfg = Config(alembic_cfg_path)
            
            # Override the database URL in the config
            alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)
            
            # Run upgrade
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic upgrade completed successfully")
            _migrations_completed = True
            
        except Exception as e:
            logger.error(f"Alembic upgrade failed: {e}")
            raise


async def test_database_connection():
    """Test database connectivity."""
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False


async def init_database():
    """Initialize database with migrations."""
    try:
        # Test connection first
        if not await test_database_connection():
            raise Exception("Database connection failed")
        
        # Run Alembic migrations synchronously (no await needed)
        logger.info("Running database migrations...")
        run_alembic_upgrade()
        
        logger.info("Database initialization completed successfully")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


async def close_database():
    """Dispose platform + tenant async pools."""
    await close_database_pools()