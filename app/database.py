"""
Database connection — async SQLAlchemy + asyncpg for PostgreSQL RDS
"""

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData

class Base(DeclarativeBase):
    metadata = MetaData(schema="public")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./community_fundings.db",
)

DATABASE_URL_SYNC = os.getenv(
    "DATABASE_URL_SYNC",
    DATABASE_URL.replace("+asyncpg", "+psycopg"),
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "server_settings": {
            "search_path": "public"
        }
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("SET search_path TO public"))
        # await conn.run_sync(Base.metadata.create_all)
