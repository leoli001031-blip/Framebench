import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase
from backend.config import DB_PATH, SECRET_SETTING_KEYS

# Ensure parent directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def get_system_setting(key: str, default: str = "") -> str:
    """Helper to fetch a setting from the database."""
    from backend.models import SystemSetting
    from backend.services.secret_store import resolve_secret_value
    async with AsyncSessionLocal() as session:
        result = await session.get(SystemSetting, key)
        if result:
            if key in SECRET_SETTING_KEYS:
                return await asyncio.to_thread(resolve_secret_value, key, result.value, default)
            return result.value or default
        return default
