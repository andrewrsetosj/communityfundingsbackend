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
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./community_fundings.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")

# Create an async engine but DO NOT call create_all() automatically.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    **({} if _is_sqlite else {"pool_pre_ping": True, "pool_recycle": 300}),
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