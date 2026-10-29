import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.history import ChatMessage


class Storage:
    def __init__(self, db_path: Optional[str] = None):
        # PyInstaller tek dosya uygulaması her açılışta geçici bir klasöre
        # çıkarılır. Veritabanını proje klasöründe tutmak belleği kaybettirir;
        # bu nedenle kalıcı kullanıcı uygulama dizinini kullanıyoruz.
        app_data_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Jarvis"
        default_dir = app_data_root / "data"
        default_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = Path(db_path) if db_path else default_dir / "jarvis.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()

    def close(self) -> None:
        """Veritabanı bağlantısını özellikle test/kapanış akışlarında kapatır."""
        if getattr(self, "connection", None) is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _create_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                pause_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                tool TEXT NOT NULL,
                args_summary TEXT,
                result TEXT,
                status TEXT NOT NULL,
                screenshot TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES task_sessions(id)
            )
            """
        )
        self.connection.commit()
        self._ensure_schema_migrations()

    def _table_columns(self, table: str) -> set:
        cursor = self.connection.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in cursor.fetchall()}

    def _ensure_schema_migrations(self) -> None:
        if "metadata" not in self._table_columns("messages"):
            self.connection.execute("ALTER TABLE messages ADD COLUMN metadata TEXT")
            self.connection.commit()
        self._migrate_agent_runs_table()

    def _migrate_agent_runs_table(self) -> None:
        columns = self._table_columns("agent_runs")
        if not columns or {"prompt", "finished_at"} <= columns:
            return

        prompt_column = "prompt" if "prompt" in columns else "task"
        finished_column = (
            "finished_at" if "finished_at" in columns
            else "updated_at" if "updated_at" in columns
            else None
        )
        prompt_expr = prompt_column if prompt_column in columns else "''"
        status_expr = "COALESCE(status, 'completed')" if "status" in columns else "'completed'"
        summary_expr = "summary" if "summary" in columns else "NULL"
        created_expr = "COALESCE(created_at, ?)" if "created_at" in columns else "?"
        finished_expr = finished_column if finished_column else "NULL"
        now = datetime.now(timezone.utc).isoformat()

        # Eski şema verisini kaybetmeden yeniden inşa ediyoruz; geri dönüş
        # gerekirse diye dosya kopyası migration'dan önce alınır.
        backup_path = self.db_path.with_name(self.db_path.name + ".pre-migration.bak")
        if not backup_path.exists():
            backup = sqlite3.connect(str(backup_path))
            try:
                self.connection.backup(backup)
                backup.commit()
            finally:
                backup.close()

        foreign_keys_on = bool(
            self.connection.execute("PRAGMA foreign_keys").fetchone()[0]
        )
        if foreign_keys_on:
            self.connection.execute("PRAGMA foreign_keys = OFF")

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("DROP TABLE IF EXISTS agent_runs_migrating")
            self.connection.execute(
                """
                CREATE TABLE agent_runs_migrating (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            self.connection.execute(
                f"""
                INSERT INTO agent_runs_migrating (id, prompt, status, summary, created_at, finished_at)
                SELECT id, COALESCE({prompt_expr}, ''), {status_expr}, {summary_expr}, {created_expr}, {finished_expr}
                FROM agent_runs
                """,
                (now,),
            )
            self.connection.execute("DROP TABLE agent_runs")
            self.connection.execute("ALTER TABLE agent_runs_migrating RENAME TO agent_runs")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            if foreign_keys_on:
                self.connection.execute("PRAGMA foreign_keys = ON")

    def add_message(
        self,
        role: str,
        content: str,
        source: str = "chat",
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO messages (role, content, source, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (role, content, source, json.dumps(metadata) if metadata else None, created_at),
        )
        self.connection.commit()

    def get_recent_messages(self, limit: int = 50) -> List[ChatMessage]:
        cursor = self.connection.execute(
            "SELECT role, content, source, metadata, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = [self._row_to_dict(row) for row in cursor.fetchall()][::-1]
        return [ChatMessage.from_row(row) for row in rows]

    def search_messages(self, query: str, limit: int = 100) -> List[ChatMessage]:
        text = str(query or "").strip()
        if not text:
            return self.get_recent_messages(limit)
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        cursor = self.connection.execute(
            "SELECT role, content, source, metadata, created_at FROM messages "
            "WHERE content LIKE ? ESCAPE '\\' OR role LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT ?",
            (pattern, pattern, max(1, int(limit))),
        )
        rows = [self._row_to_dict(row) for row in cursor.fetchall()][::-1]
        return [ChatMessage.from_row(row) for row in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "role": row["role"],
            "content": row["content"],
            "source": row["source"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            "created_at": row["created_at"],
        }

    def get_conversation_history(self, limit: int = 50, exclude_last: bool = False) -> List[ChatMessage]:
        messages = self.get_recent_messages(limit)
        if exclude_last and messages:
            return messages[:-1]
        return messages

    def start_agent_run(self, prompt: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO agent_runs (prompt, status, created_at) VALUES (?, ?, ?)",
            (prompt, "running", datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def log_agent_event(self, run_id: int, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.connection.execute(
            "INSERT INTO agent_events (run_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                run_id,
                event_type,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def finish_agent_run(self, run_id: int, status: str, summary: Optional[str] = None) -> None:
        self.connection.execute(
            "UPDATE agent_runs SET status = ?, summary = ?, finished_at = ? WHERE id = ?",
            (status, summary, datetime.now(timezone.utc).isoformat(), run_id),
        )
        self.connection.commit()

    def get_agent_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        cursor = self.connection.execute(
            "SELECT id, prompt, status, summary, created_at, finished_at "
            "FROM agent_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_agent_events(self, run_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        cursor = self.connection.execute(
            "SELECT id, event_type, payload, created_at FROM agent_events "
            "WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (int(run_id), limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ---------------------------------------------------------- görev oturumları
    def start_task_session(self, goal: str, run_id: Optional[int] = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO task_sessions (run_id, goal, status, created_at, updated_at) "
            "VALUES (?, ?, 'running', ?, ?)",
            (run_id, str(goal), now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_task_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT * FROM task_sessions WHERE id = ?", (int(session_id),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_active_task_session(self) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT * FROM task_sessions WHERE status IN ('running', 'paused', 'takeover') "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_latest_task_session(self) -> Optional[Dict[str, Any]]:
        cursor = self.connection.execute(
            "SELECT * FROM task_sessions ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_task_sessions(self, limit: int = 12) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        cursor = self.connection.execute(
            "SELECT * FROM task_sessions ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def set_task_session_state(
        self,
        session_id: int,
        status: str,
        pause_reason: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        finished = now if status in {"completed", "cancelled", "error"} else None
        self.connection.execute(
            "UPDATE task_sessions SET status = ?, pause_reason = ?, summary = COALESCE(?, summary), "
            "updated_at = ?, finished_at = COALESCE(?, finished_at) WHERE id = ?",
            (status, pause_reason, summary, now, finished, int(session_id)),
        )
        self.connection.commit()

    def add_task_step(
        self,
        session_id: int,
        seq: int,
        tool: str,
        args_summary: str,
        result: str,
        status: str,
        screenshot: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO task_steps (session_id, seq, tool, args_summary, result, status, screenshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(session_id),
                int(seq),
                str(tool),
                str(args_summary)[:2000],
                str(result)[:4000],
                str(status),
                screenshot,
                now,
            ),
        )
        self.connection.execute(
            "UPDATE task_sessions SET updated_at = ? WHERE id = ?", (now, int(session_id))
        )
        self.connection.commit()

    def get_task_steps(self, session_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        cursor = self.connection.execute(
            "SELECT * FROM task_steps WHERE session_id = ? ORDER BY seq ASC, id ASC LIMIT ?",
            (int(session_id), limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def save_memory(self, key: str, value: str) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, updated_at),
        )
        self.connection.commit()

    def get_memory(self, key: str) -> Optional[str]:
        cursor = self.connection.execute(
            "SELECT value FROM memory WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row["value"] if row else None

    def delete_memory(self, key: str) -> None:
        self.connection.execute(
            "DELETE FROM memory WHERE key = ?", (key,)
        )
        self.connection.commit()

    def get_all_memory(self) -> List[Dict[str, str]]:
        cursor = self.connection.execute(
            "SELECT key, value, updated_at FROM memory ORDER BY updated_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]
