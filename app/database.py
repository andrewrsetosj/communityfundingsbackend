# """
# Database connection — async SQLAlchemy + asyncpg for PostgreSQL RDS
# """

# import os
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
# from sqlalchemy.orm import DeclarativeBase
# print("DATABASE_URL used by app:", os.getenv("DATABASE_URL"))

# DATABASE_URL = os.getenv(
#     "DATABASE_URL",
#     "sqlite+aiosqlite:///./community_fundings.db",
# )

# DATABASE_URL_SYNC = os.getenv(
#     "DATABASE_URL_SYNC",
#     DATABASE_URL.replace("+asyncpg", "+psycopg"),
# )

# engine = create_async_engine(
#     DATABASE_URL,
#     echo=False,
#     pool_pre_ping=True,
#     pool_recycle=300,
#     future=True,
# )

# AsyncSessionLocal = async_sessionmaker(
#     engine,
#     class_=AsyncSession,
#     expire_on_commit=False,
# )


# class Base(DeclarativeBase):
#     pass


# async def get_db():
#     async with AsyncSessionLocal() as session:
#         async with session.begin():
#             yield session


# async def init_db():
#     async with engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)
"""
Minimal SQLAlchemy compatibility shim so other modules can import `get_db`.
This intentionally does NOT run Base.metadata.create_all() to avoid touching
other tables in the production DB. If you want full ORM table creation,
re-enable create_all or use Alembic migrations.
"""

import os
import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

# Normalize DB URL so async SQLAlchemy always gets an async driver.
# Supports env values like:
# - postgresql://...
# - postgres://...
# - postgresql+asyncpg://...
_raw_url = (os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./community_fundings.db").strip().strip('"').strip("'")
if _raw_url.startswith("postgres://"):
    _raw_url = "postgresql://" + _raw_url[len("postgres://"):]
if _raw_url.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + _raw_url[len("postgresql://"):]
else:
    DATABASE_URL = _raw_url

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args: dict = {}

# asyncpg does not accept sslmode query param directly; map it to connect_args["ssl"].
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    parsed = urlsplit(DATABASE_URL)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    sslmode = None
    kept_items = []
    for k, v in query_items:
        if k.lower() == "sslmode":
            sslmode = (v or "").lower()
        else:
            kept_items.append((k, v))

    if sslmode is not None:
        DATABASE_URL = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(kept_items), parsed.fragment)
        )
        if sslmode not in ("disable", "allow", "prefer"):
            ctx = ssl.create_default_context()
            # For "require", skip cert verification (matches common local RDS setups).
            if sslmode == "require":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            _connect_args["ssl"] = ctx

# Create an async engine but DO NOT call create_all() automatically.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **(
        {}
        if _is_sqlite
        else {
            "pool_pre_ping": True,
            "pool_recycle": 300,
            **({"connect_args": _connect_args} if _connect_args else {}),
        }
    ),
)

# Async session maker to be used by get_db dependency
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

# keep init_db as a no-op to avoid create_all() touching other tables
async def init_db():
    return

# Dependency for FastAPI routes that expect a SQLAlchemy session
async def get_db():
    """
    Yields an AsyncSession for routes that depend on SQLAlchemy-style sessions.
    If your code uses asyncpg (db.py) instead, you can remove these routes'
    dependency on get_db and call asyncpg helpers directly.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session