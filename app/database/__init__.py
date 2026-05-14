"""
PostgreSQL connectivity stack:

- engines — async pools (platform registry + per-tenant databases)
- session — FastAPI dependencies (`get_db_session`, `get_platform_db_session`)
- routing — maps HTTP requests to platform vs tenant DB
- tenant_context — cached `hospitals.tenant_database_name` lookups
- async_ssl — asyncpg TLS connect_args
- ssl_connect — psycopg2 TLS for migrations and sync DDL

Prefer importing session helpers from ``app.database.session`` or ``app.core.database``.
"""
