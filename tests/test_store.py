import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker.store import Store


class StoreTests(unittest.TestCase):
    def test_startup_removes_legacy_proposals_and_generated_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "tracker.db"
            store = Store(database_path)
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE proposals (
                        proposal_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        repository_version TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO proposals VALUES (?, ?, ?, ?, ?)",
                    ("old", "orders", "commit-1", "{}", "2026-01-01T00:00:00Z"),
                )
            store.add_memory("orders", "requirement", "旧方案需求", "proposal:old", "commit-1")
            store.add_memory("orders", "constraint", "必须保留审计日志", "产品确认", "commit-1")

            migrated = Store(database_path)

            with sqlite3.connect(database_path) as connection:
                proposal_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'proposals'"
                ).fetchone()
            self.assertIsNone(proposal_table)
            self.assertEqual(
                [item["content"] for item in migrated.list_memory("orders", include_stale=True)],
                ["必须保留审计日志"],
            )


if __name__ == "__main__":
    unittest.main()
