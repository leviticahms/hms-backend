"""
Application configuration settings.
Manages environment variables and application settings.
"""
from pydantic_settings import BaseSettings
from pydantic import AliasChoices, Field, model_validator
from typing import Any, Literal, Optional
import json
import os
import logging
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _ensure_minimal_logging() -> None:
    """So import-time messages in this module are visible before main configures logging."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


_ensure_minimal_logging()

# Force load env file for local development
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"
ALT_ENV_FILE = BASE_DIR / "env"

if os.getenv("RENDER", "").lower() not in {"true", "1"}:
    if ENV_FILE.exists():
        load_dotenv(dotenv_path=ENV_FILE, override=True)
        logger.info(f"✓ Loaded .env from {ENV_FILE}")
    elif ALT_ENV_FILE.exists():
        load_dotenv(dotenv_path=ALT_ENV_FILE, override=True)
        logger.info(f"✓ Loaded env file from {ALT_ENV_FILE}")
    else:
        logger.warning(f"✗ .env not found at {ENV_FILE} (and no fallback env file)")
else:
    logger.info("Running in Render - using environment variables")


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Application
    APP_NAME: str = "Hospital Management SaaS"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(default=False, env="DEBUG")
    OPENAPI_DOCS: bool = Field(default=True, env="OPENAPI_DOCS")
    
    # Database Configuration
    DB_HOST: str = Field(default="localhost", env="DB_HOST")
    DB_PORT: int = Field(default=5432, env="DB_PORT")
    DB_USER: str = Field(default="abc", env="DB_USER")
    DB_PASSWORD: str = Field(default="abc", env="DB_PASSWORD")
    DB_NAME: str = Field(default="abc", env="DB_NAME")
    
    # Database Pool Configuration
    DB_POOL_SIZE: int = Field(default=5, env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int = Field(default=10, env="DB_MAX_OVERFLOW")
    TENANT_DB_POOL_SIZE: int = Field(
        default=5,
        env="TENANT_DB_POOL_SIZE",
        description="Async pool size per dedicated tenant PostgreSQL database.",
    )
    TENANT_DB_MAX_OVERFLOW: int = Field(
        default=10,
        env="TENANT_DB_MAX_OVERFLOW",
        description="Max overflow connections per tenant database pool.",
    )
    
    # Database URLs - Direct from environment
    DATABASE_URL: str = Field(default="", env="DATABASE_URL")
    DATABASE_URL_SYNC: str = Field(default="", env="DATABASE_URL_SYNC")
    DB_PRUNE_UNUSED_TABLES: bool = Field(default=False, env="DB_PRUNE_UNUSED_TABLES")
    DB_BOOTSTRAP_FROM_MODELS: bool = Field(default=True, env="DB_BOOTSTRAP_FROM_MODELS")
    # Dev / private networks only: skip TLS certificate verification for Postgres (asyncpg + psycopg2)
    DATABASE_SSL_INSECURE: bool = Field(default=False, env="DATABASE_SSL_INSECURE")
    # When true, asyncpg/psycopg2 verify the Postgres server certificate (strict). On Render the default
    # is false so TLS still encrypts but chain verification is skipped (avoids SSLCertVerificationError).
    DATABASE_SSL_VERIFY: bool = Field(default=False, env="DATABASE_SSL_VERIFY")
    # Corporate TLS proxies / misconfigured SMTP: skip cert verify for aiosmtplib
    SMTP_TLS_INSECURE: bool = Field(default=False, env="SMTP_TLS_INSECURE")

    # Per-hospital PostgreSQL databases (same server as DATABASE_URL / pgAdmin)
    TENANT_DB_AUTO_PROVISION: bool = Field(
        default=True,
        env="TENANT_DB_AUTO_PROVISION",
        description=(
            "When True, each new hospital gets CREATE DATABASE on the same Postgres server "
            "(DB user needs CREATEDB). Set false for a single shared database only."
        ),
    )
    TENANT_DB_NAME_PREFIX: str = Field(
        default="hosp_",
        env="TENANT_DB_NAME_PREFIX",
        description="Prefix for tenant DB names (e.g. hosp_<uuid_hex>)",
    )
    TENANT_TEMPLATE_DATABASE: str = Field(
        default="",
        env="TENANT_TEMPLATE_DATABASE",
        description="Optional: clone new tenant DBs from this template (prepare once with schema)",
    )
    TENANT_DB_ADMIN_DATABASE: str = Field(
        default="postgres",
        env="TENANT_DB_ADMIN_DATABASE",
        description="Database used for CREATE DATABASE sessions (local default: postgres).",
    )
    TENANT_DB_ROUTE_QUERIES: bool = Field(
        default=True,
        env="TENANT_DB_ROUTE_QUERIES",
        description="If True, hospital-scoped API requests use that hospital's dedicated database when tenant_database_name is set",
    )

    # Redis / Caching
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    
    # Super Admin Configuration (seed skips user creation until email + password are set in env)
    SUPERADMIN_EMAIL: str = Field(default="", env="SUPERADMIN_EMAIL")
    SUPERADMIN_PASSWORD: str = Field(default="", env="SUPERADMIN_PASSWORD")
    SUPERADMIN_FIRST_NAME: str = Field(default="", env="SUPERADMIN_FIRST_NAME")
    SUPERADMIN_LAST_NAME: str = Field(default="", env="SUPERADMIN_LAST_NAME")

    # Stable platform bootstrap hospital row (optional; derived from HOSPITAL_EMAIL if unset)
    PLATFORM_REGISTRATION_NUMBER: str = Field(default="", env="PLATFORM_REGISTRATION_NUMBER")
    
    # Security
    SECRET_KEY: str = Field(default="", env="SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 5 * 24 *60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS — set ALLOWED_ORIGINS in env (comma-separated or JSON array); empty = no browser origins
    # Env binds to allowed_origins_str (str) so pydantic-settings does not JSON-decode list fields.
    allowed_origins_str: str = Field(
        default="",
        validation_alias=AliasChoices("ALLOWED_ORIGINS"),
    )
    cors_allowed_origins: list[str] = Field(default_factory=list)
    # Optional frontend URL convenience (auto-added to ALLOWED_ORIGINS if set)
    FRONTEND_URL: str = Field(default="", env="FRONTEND_URL")
    VERCEL_URL: str = Field(
        default="",
        env="VERCEL_URL",
        description="Optional Vercel hostname (without scheme). If set, will be converted to https://<host> for CORS.",
    )

    @staticmethod
    def _parse_allowed_origins(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            if raw == "*":
                return ["*"]
            return [item.strip() for item in raw.split(",") if item.strip()]
        return []

    @model_validator(mode="after")
    def _add_frontend_origin(self):
        """
        Convenience: allow setting FRONTEND_URL / VERCEL_URL without manually editing ALLOWED_ORIGINS.
        - FRONTEND_URL can be a full origin (https://...) or a bare host.
        - VERCEL_URL is usually a bare host like: hospital-management-12.vercel.app
        """
        origins = self._parse_allowed_origins(self.allowed_origins_str)
        if not origins and self.cors_allowed_origins:
            origins = list(self.cors_allowed_origins)
        if "*" in origins:
            self.cors_allowed_origins = origins
            return self

        def _norm_origin(raw: str) -> str:
            r = (raw or "").strip()
            if not r:
                return ""
            if r.startswith("http://") or r.startswith("https://"):
                return r.rstrip("/")
            return f"https://{r.rstrip('/')}"

        fe = _norm_origin(self.FRONTEND_URL)
        if fe and fe not in origins:
            origins.append(fe)

        vz = _norm_origin(self.VERCEL_URL)
        if vz and vz not in origins:
            origins.append(vz)

        self.cors_allowed_origins = origins
        return self

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        return self.cors_allowed_origins

    # Public URL of this backend (used for absolute /uploads/... links in JSON; set to your API origin)
    APP_PUBLIC_URL: str = Field(default="http://localhost:8000", env="APP_PUBLIC_URL")
    
    # Email — optional SendGrid HTTP API (notifications module); app mail uses SMTP below
    SENDGRID_API_KEY: str = Field(default="", env="SENDGRID_API_KEY")
    EMAIL_FROM: str = Field(
        default="",
        env="EMAIL_FROM",
        description="Verified sender address (required by Brevo etc.). If empty, code falls back to SMTP_USER.",
    )
    
    # SMTP (default: Brevo — smtp-relay.brevo.com, port 587 STARTTLS; SMTP_USER + SMTP key from Brevo dashboard)
    SMTP_HOST: str = Field(default="smtp-relay.brevo.com", env="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, env="SMTP_PORT")
    SMTP_USER: str = Field(default="", env="SMTP_USER")
    SMTP_PASS: str = Field(default="", env="SMTP_PASS")
    
    # File Upload
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"
    ALLOWED_FILE_TYPES: list = [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"]
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # Logging — see app.core.logging_config.configure_logging
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FORMAT: Literal["text", "json"] = Field(default="text", env="LOG_FORMAT")
    LOG_DATE_FORMAT: str = Field(
        default="%Y-%m-%d %H:%M:%S",
        env="LOG_DATE_FORMAT",
        description="strftime format for the ts field (text and json).",
    )
    LOG_TEXT_FORMAT: str = Field(
        default="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        env="LOG_TEXT_FORMAT",
        description="Python logging format string when LOG_FORMAT=text.",
    )
    LOG_MODULE_LEVELS: str = Field(
        default="",
        env="LOG_MODULE_LEVELS",
        description=(
            "Comma-separated logger prefix=LEVEL overrides, e.g. "
            "'app.middleware=DEBUG,app.api.v1.routers.auth=INFO'"
        ),
    )
    LOG_UVICORN_ACCESS: bool = Field(default=True, env="LOG_UVICORN_ACCESS")
    LOG_UVICORN_LEVEL: str = Field(default="INFO", env="LOG_UVICORN_LEVEL")
    LOG_UVICORN_ACCESS_LEVEL: str = Field(
        default="INFO",
        env="LOG_UVICORN_ACCESS_LEVEL",
        description="Level for uvicorn.access when LOG_UVICORN_ACCESS=true.",
    )
    LOG_SQLALCHEMY_LEVEL: str = Field(
        default="WARNING",
        env="LOG_SQLALCHEMY_LEVEL",
        description="sqlalchemy.engine log level (set DEBUG for SQL text).",
    )
    LOG_SQLALCHEMY_POOL_LEVEL: str = Field(
        default="WARNING",
        env="LOG_SQLALCHEMY_POOL_LEVEL",
    )
    LOG_ASYNCPG_LEVEL: str = Field(
        default="WARNING",
        env="LOG_ASYNCPG_LEVEL",
    )
    
    # Hospital / platform bootstrap (required in env to create the SuperAdmin-linked platform hospital row)
    HOSPITAL_NAME: str = Field(default="", env="HOSPITAL_NAME")
    HOSPITAL_ADDRESS: str = Field(default="", env="HOSPITAL_ADDRESS")
    HOSPITAL_PHONE: str = Field(default="", env="HOSPITAL_PHONE")
    HOSPITAL_EMAIL: str = Field(default="", env="HOSPITAL_EMAIL")
    HOSPITAL_CITY: str = Field(default="", env="HOSPITAL_CITY")
    HOSPITAL_STATE: str = Field(default="", env="HOSPITAL_STATE")
    HOSPITAL_COUNTRY: str = Field(default="", env="HOSPITAL_COUNTRY")
    HOSPITAL_PINCODE: str = Field(default="", env="HOSPITAL_PINCODE")

    # IPD scheduler: daily bed line item amount when ward-specific pricing is not resolved yet (0 = skip posting)
    IPD_DEFAULT_BED_RATE_PER_DAY: float = Field(default=0.0, env="IPD_DEFAULT_BED_RATE_PER_DAY")
    
    # PDF Storage
    PDF_STORAGE_PATH: str = Field(default="./pdfs", env="PDF_STORAGE_PATH")
    
    # SMS Configuration (Twilio)
    TWILIO_ACCOUNT_SID: str = Field(default="", env="TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = Field(default="", env="TWILIO_AUTH_TOKEN")
    TWILIO_FROM_NUMBER: str = Field(default="", env="TWILIO_FROM_NUMBER")
    
    # Payment Gateways
    STRIPE_SECRET_KEY: str = Field(default="", env="STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: str = Field(default="", env="STRIPE_WEBHOOK_SECRET")
    STRIPE_PUBLISHABLE_KEY: str = Field(default="", env="STRIPE_PUBLISHABLE_KEY")
    
    RAZORPAY_KEY_ID: str = Field(default="", env="RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", env="RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", env="RAZORPAY_WEBHOOK_SECRET")
    
    PAYTM_MID: str = Field(default="", env="PAYTM_MID")
    PAYTM_KEY: str = Field(default="", env="PAYTM_KEY")
    PAYTM_ENV: str = Field(default="sandbox", env="PAYTM_ENV")
    PAYTM_WEBSITE: str = Field(default="WEBSTAGING", env="PAYTM_WEBSITE")
    PAYTM_CALLBACK_URL: str = Field(default="", env="PAYTM_CALLBACK_URL")

    # Public demo request
    DEMO_REQUEST_NOTIFY_EMAIL: str = Field(default="", env="DEMO_REQUEST_NOTIFY_EMAIL")
    DEMO_REQUEST_SEND_CONFIRMATION: bool = Field(default=True, env="DEMO_REQUEST_SEND_CONFIRMATION")
    CONTACT_MESSAGE_NOTIFY_EMAIL: str = Field(default="", env="CONTACT_MESSAGE_NOTIFY_EMAIL")
    CONTACT_MESSAGE_SEND_ACK: bool = Field(default=True, env="CONTACT_MESSAGE_SEND_ACK")

    @model_validator(mode="after")
    def normalize_database_urls(self):
        """Normalize database URLs"""
        async_url = (self.DATABASE_URL or "").strip()
        sync_url = (self.DATABASE_URL_SYNC or "").strip()

        if not async_url and not sync_url:
            # Build from DB_* so local runs work without DATABASE_URL in .env (defaults match Field defaults).
            u = quote_plus(self.DB_USER or "")
            p = quote_plus(self.DB_PASSWORD or "")
            host = self.DB_HOST or "localhost"
            port = int(self.DB_PORT) if self.DB_PORT else 5432
            dbn = (self.DB_NAME or "postgres").strip() or "postgres"
            sync_url = f"postgresql://{u}:{p}@{host}:{port}/{dbn}"
            async_url = f"postgresql+asyncpg://{u}:{p}@{host}:{port}/{dbn}"

        if async_url and not sync_url:
            sync_url = self._to_sync_url(async_url)
            # DATABASE_URL may be mistakenly set to a sync driver (e.g. +psycopg2); always coerce async.
            async_url = self._to_async_url(async_url)
        elif sync_url and not async_url:
            async_url = self._to_async_url(sync_url)
        else:
            async_url = self._to_async_url(async_url)
            sync_url = self._to_sync_url(sync_url)
            if self._is_local_url(async_url) and not self._is_local_url(sync_url):
                async_url = self._to_async_url(sync_url)
            elif self._is_local_url(sync_url) and not self._is_local_url(async_url):
                sync_url = self._to_sync_url(async_url)

        self.DATABASE_URL = Settings._strip_libpq_only_params_from_asyncpg_url(async_url)
        self.DATABASE_URL_SYNC = sync_url
        return self

    @model_validator(mode="after")
    def require_secret_outside_debug(self):
        key = (self.SECRET_KEY or "").strip()
        if not self.DEBUG and not key:
            raise ValueError("SECRET_KEY must be set when DEBUG=False")
        return self

    @staticmethod
    def _strip_libpq_only_params_from_asyncpg_url(url: str) -> str:
        """
        asyncpg.connect() does not accept libpq query keys (e.g. sslmode, sslrootcert).
        Render's DATABASE_URL often includes ?sslmode=require — keep that on the sync URL
        for psycopg2, but drop it from postgresql+asyncpg URLs (SSL is via connect_args / ssl).
        """
        value = (url or "").strip()
        if not value or "postgresql+asyncpg://" not in value:
            return value
        try:
            parsed = urlparse(value)
            if not parsed.query:
                return value
            skip = frozenset(
                name.lower()
                for name in (
                    "sslmode",
                    "sslrootcert",
                    "sslcert",
                    "sslkey",
                    "sslcrl",
                    "sslcompression",
                    "ssl_min_protocol_version",
                    "ssl_max_protocol_version",
                    "gssencmode",
                    "krbsrvname",
                    "channel_binding",
                )
            )
            pairs = [
                (k, v)
                for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() not in skip
            ]
            new_query = urlencode(pairs)
            return urlunparse(parsed._replace(query=new_query))
        except Exception:
            logger.warning("Could not sanitize asyncpg URL query; using original", exc_info=True)
            return value

    @staticmethod
    def _to_async_url(url: str) -> str:
        value = (url or "").strip()
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql+psycopg2://"):
            value = value.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql+psycopg://"):
            value = value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return Settings._strip_libpq_only_params_from_asyncpg_url(value)

    @staticmethod
    def _to_sync_url(url: str) -> str:
        value = (url or "").strip()
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql://", 1)
        elif value.startswith("postgresql+asyncpg://"):
            value = value.replace("postgresql+asyncpg://", "postgresql://", 1)
        elif value.startswith("postgresql+psycopg2://"):
            value = value.replace("postgresql+psycopg2://", "postgresql://", 1)
        elif value.startswith("postgresql+psycopg://"):
            value = value.replace("postgresql+psycopg://", "postgresql://", 1)
        return value

    @staticmethod
    def _is_local_url(url: str) -> bool:
        value = (url or "").strip()
        if not value:
            return True
        try:
            host = (urlparse(value).hostname or "").lower()
        except Exception:
            return False
        return host in {"localhost", "127.0.0.1", "::1"}
    
    @property
    def database_url(self) -> str:
        return self.DATABASE_URL
    
    @property
    def database_url_sync(self) -> str:
        return self.DATABASE_URL_SYNC
    
    @property
    def sync_database_url(self) -> str:
        return self.DATABASE_URL_SYNC
    
    def log_config(self) -> None:
        """Log configuration details (mask sensitive data)"""
        import re
        masked_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL)
        masked_sync_url = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', self.DATABASE_URL_SYNC)
        
        logger.info("=" * 60)
        logger.info("Configuration Loaded:")
        logger.info(f"  Database Host: {self.DB_HOST}")
        logger.info(f"  Database Name: {self.DB_NAME}")
        logger.info(f"  Async URL: {masked_url}")
        logger.info(f"  Sync URL: {masked_sync_url}")
        logger.info(f"  SMTP: {self.SMTP_HOST}:{self.SMTP_PORT} (credentials {'SET' if (self.SMTP_USER and self.SMTP_PASS) else 'NOT SET ⚠️'})")
        logger.info(f"  SendGrid API Key (optional): {'SET (' + self.SENDGRID_API_KEY[:20] + '...)' if self.SENDGRID_API_KEY else 'NOT SET'}")
        logger.info(f"  Email From: {self.EMAIL_FROM}")
        logger.info(f"  Contact Notify: {self.CONTACT_MESSAGE_NOTIFY_EMAIL or 'Using SUPERADMIN_EMAIL'}")
        logger.info(
            "  Logging: level=%s format=%s uvicorn_access=%s sqlalchemy_engine=%s",
            self.LOG_LEVEL,
            self.LOG_FORMAT,
            self.LOG_UVICORN_ACCESS,
            self.LOG_SQLALCHEMY_LEVEL,
        )
        if (self.LOG_MODULE_LEVELS or "").strip():
            logger.info("  LOG_MODULE_LEVELS: %s", self.LOG_MODULE_LEVELS)
        logger.info("=" * 60)
    
    class Config:
        # Don't use env_file in Config - we already loaded it above
        case_sensitive = True
        # Allow extra fields for future compatibility
        extra = "ignore"


# Global settings instance
settings = Settings()