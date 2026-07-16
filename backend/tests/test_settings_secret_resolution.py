import unittest
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import SystemSetting
from backend.services.secret_store import KEYCHAIN_MARKER


class SettingsSecretResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_get_system_setting_resolves_keychain_marker(self):
        from backend import database

        async with self.session_factory() as db:
            db.add(SystemSetting(key="analysis_api_key", value=KEYCHAIN_MARKER))
            await db.commit()

        with (
            patch.object(database, "AsyncSessionLocal", self.session_factory),
            patch("backend.services.secret_store.get_keychain_secret", return_value="secret-value"),
        ):
            value = await database.get_system_setting("analysis_api_key")

        self.assertEqual(value, "secret-value")


if __name__ == "__main__":
    unittest.main()
