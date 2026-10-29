import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.storage import Storage


class AgentRunStorageTests(unittest.TestCase):
    def test_agent_run_and_events_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            with Storage(str(Path(directory) / "jarvis.db")) as storage:
                run_id = storage.start_agent_run("Masaüstünde örnek görev")
                storage.log_agent_event(run_id, "tool_result", {"ok": True, "tool": "create_folder"})
                storage.finish_agent_run(run_id, "completed", "Görev tamamlandı")

                run = storage.connection.execute(
                    "SELECT status, summary FROM agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
                event = storage.connection.execute(
                    "SELECT event_type, payload FROM agent_events WHERE run_id = ?", (run_id,)
                ).fetchone()

                self.assertEqual(run["status"], "completed")
                self.assertEqual(run["summary"], "Görev tamamlandı")
                self.assertEqual(event["event_type"], "tool_result")
                self.assertIn("create_folder", event["payload"])

                runs = storage.get_agent_runs(5)
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["prompt"], "Masaüstünde örnek görev")
                self.assertEqual(runs[0]["status"], "completed")
                events = storage.get_agent_events(run_id, 5)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "tool_result")


class AgentRunMigrationTests(unittest.TestCase):
    def test_legacy_agent_runs_schema_is_rebuilt_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "jarvis.db"
            legacy = sqlite3.connect(str(db_path))
            try:
                legacy.executescript(
                    """
                    CREATE TABLE agent_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task TEXT NOT NULL,
                        status TEXT NOT NULL,
                        summary TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT
                    );
                    CREATE TABLE agent_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        payload TEXT,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO agent_runs (task, status, summary, created_at, updated_at)
                    VALUES ('Eski görev', 'completed', 'Eski özet',
                            '2026-01-01T00:00:00+00:00', '2026-01-01T00:05:00+00:00');
                    INSERT INTO agent_events (run_id, event_type, payload, created_at)
                    VALUES (1, 'tool_result', '{"tool": "create_folder"}',
                            '2026-01-01T00:01:00+00:00');
                    """
                )
                legacy.commit()
            finally:
                legacy.close()

            with Storage(str(db_path)) as storage:
                columns = {
                    row["name"]
                    for row in storage.connection.execute("PRAGMA table_info(agent_runs)")
                }
                self.assertIn("prompt", columns)
                self.assertIn("finished_at", columns)

                run = storage.connection.execute(
                    "SELECT prompt, status, summary, finished_at FROM agent_runs WHERE id = 1"
                ).fetchone()
                self.assertEqual(run["prompt"], "Eski görev")
                self.assertEqual(run["status"], "completed")
                self.assertEqual(run["summary"], "Eski özet")
                self.assertEqual(run["finished_at"], "2026-01-01T00:05:00+00:00")

                new_run_id = storage.start_agent_run("Yeni görev")
                self.assertGreater(new_run_id, 1)
                event = storage.connection.execute(
                    "SELECT event_type FROM agent_events WHERE run_id = 1"
                ).fetchone()
                self.assertEqual(event["event_type"], "tool_result")

            backup = Path(str(db_path) + ".pre-migration.bak")
            self.assertTrue(backup.exists())
            backup_connection = sqlite3.connect(str(backup))
            try:
                legacy_row = backup_connection.execute(
                    "SELECT task FROM agent_runs WHERE id = 1"
                ).fetchone()
                self.assertEqual(legacy_row[0], "Eski görev")
            finally:
                backup_connection.close()


if __name__ == "__main__":
    unittest.main()
