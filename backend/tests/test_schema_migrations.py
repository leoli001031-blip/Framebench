import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.database import Base
from backend.main import _migrate_schema_v2


class SchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_migration_is_idempotent_for_existing_storyboard_tables(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE storyboards ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, brief TEXT NOT NULL, "
                "reference_job_ids TEXT NOT NULL)"
            ))
            await conn.execute(text(
                "CREATE TABLE storyboard_generation_tasks ("
                "id TEXT PRIMARY KEY, brief TEXT NOT NULL, reference_job_ids TEXT NOT NULL, "
                "status TEXT NOT NULL)"
            ))
            await conn.run_sync(Base.metadata.create_all)

            await _migrate_schema_v2(conn)
            await _migrate_schema_v2(conn)

            storyboard_columns = {
                row[1] for row in (await conn.execute(text("PRAGMA table_info(storyboards)"))).fetchall()
            }
            task_columns = {
                row[1]
                for row in (await conn.execute(text("PRAGMA table_info(storyboard_generation_tasks)"))).fetchall()
            }
            reference_table = await conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='reference_board_items'"
            ))

        await engine.dispose()
        self.assertIn("reference_shot_ids", storyboard_columns)
        self.assertIn("reference_shot_ids", task_columns)
        self.assertEqual(reference_table.scalar_one(), "reference_board_items")


if __name__ == "__main__":
    unittest.main()
