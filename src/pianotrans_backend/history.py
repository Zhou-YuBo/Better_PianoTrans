"""SQLite storage for local transcription attempt metadata."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import FileSnapshot


SCHEMA_VERSION = 3
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


class HistoryStorageError(RuntimeError):
    """Raised when the local history store cannot safely complete an operation."""


@dataclass(frozen=True)
class HistoryAttempt:
    history_id: str
    task_id: str
    attempt_id: str
    input_path: str
    midi_path: str
    status: str
    batch_size: int
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    queued_at: datetime | None = None
    input_sha256: str | None = None
    input_size_bytes: int | None = None
    input_mtime_ns: int | None = None
    input_duration_ms: int | None = None
    input_sample_rate: int | None = None
    input_channels: int | None = None
    output_sha256: str | None = None
    output_size_bytes: int | None = None
    output_mtime_ns: int | None = None
    app_version: str | None = None
    model_revision: str | None = None
    post_processor: str | None = None
    device_mode: str | None = None
    execution_device: str | None = None


def default_history_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "PianoTrans" / "history.db"


class HistoryRepository:
    """Owns schema migration and transactional access to one history database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or default_history_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = self._open_or_rebuild()
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def create_attempt(self, attempt: HistoryAttempt) -> None:
        if attempt.status not in ACTIVE_STATUSES:
            raise ValueError("a new attempt must be queued or running")
        self._execute_write(
            """
            INSERT INTO history_attempts (
                history_id, task_id, attempt_id, input_path, midi_path, status,
                batch_size, queued_at, started_at, finished_at, duration_ms,
                error_code, error_message, input_sha256, input_size_bytes,
                input_mtime_ns, input_duration_ms, input_sample_rate, input_channels,
                output_sha256, output_size_bytes, output_mtime_ns, app_version,
                model_revision, post_processor, device_mode, execution_device
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.history_id,
                attempt.task_id,
                attempt.attempt_id,
                attempt.input_path,
                attempt.midi_path,
                attempt.status,
                attempt.batch_size,
                _encode_datetime(attempt.queued_at or attempt.started_at),
                _encode_datetime(attempt.started_at),
                _encode_datetime(attempt.finished_at) if attempt.finished_at else None,
                attempt.duration_ms,
                attempt.error_code,
                attempt.error_message,
                attempt.input_sha256,
                attempt.input_size_bytes,
                attempt.input_mtime_ns,
                attempt.input_duration_ms,
                attempt.input_sample_rate,
                attempt.input_channels,
                attempt.output_sha256,
                attempt.output_size_bytes,
                attempt.output_mtime_ns,
                attempt.app_version,
                attempt.model_revision,
                attempt.post_processor,
                attempt.device_mode,
                attempt.execution_device,
            ),
        )

    def update_input_snapshot(
        self,
        history_id: str,
        snapshot: FileSnapshot,
        duration_ms: int | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
    ) -> None:
        self._execute_write(
            """UPDATE history_attempts SET input_sha256 = ?, input_size_bytes = ?,
               input_mtime_ns = ?, input_duration_ms = ?, input_sample_rate = ?,
               input_channels = ? WHERE history_id = ?""",
            (snapshot.sha256, snapshot.size_bytes, snapshot.mtime_ns, duration_ms, sample_rate, channels, history_id),
        )

    def update_output_snapshot(self, history_id: str, snapshot: FileSnapshot) -> None:
        self._execute_write(
            """UPDATE history_attempts SET output_sha256 = ?, output_size_bytes = ?,
               output_mtime_ns = ? WHERE history_id = ?""",
            (snapshot.sha256, snapshot.size_bytes, snapshot.mtime_ns, history_id),
        )

    def update_runtime_metadata(
        self,
        history_id: str,
        app_version: str | None,
        model_revision: str | None,
        post_processor: str | None,
    ) -> None:
        self._execute_write(
            "UPDATE history_attempts SET app_version = ?, model_revision = ?, post_processor = ? WHERE history_id = ?",
            (app_version, model_revision, post_processor, history_id),
        )

    def get_attempt(self, history_id: str) -> HistoryAttempt | None:
        row = self._connection.execute(
            "SELECT * FROM history_attempts WHERE history_id = ?", (history_id,)
        ).fetchone()
        return _row_to_attempt(row) if row is not None else None

    def update_device_metadata(
        self, history_id: str, device_mode: str | None, execution_device: str | None
    ) -> None:
        self._execute_write(
            "UPDATE history_attempts SET device_mode = ?, execution_device = ? WHERE history_id = ?",
            (device_mode, execution_device, history_id),
        )

    def mark_running(self, history_id: str, started_at: datetime | None = None) -> None:
        values: tuple[object, ...]
        query = "UPDATE history_attempts SET status = 'running'"
        if started_at is not None:
            query += ", started_at = ?"
            values = (_encode_datetime(started_at), history_id)
        else:
            values = (history_id,)
        query += " WHERE history_id = ? AND status = 'queued'"
        self._execute_write(
            query,
            values,
        )

    def finish_attempt(
        self,
        history_id: str,
        status: str,
        finished_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("a terminal status is required")
        row = self._connection.execute(
            "SELECT started_at FROM history_attempts WHERE history_id = ?", (history_id,)
        ).fetchone()
        if row is None:
            raise HistoryStorageError("history attempt does not exist")
        started_at = _decode_datetime(row[0])
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
        self._execute_write(
            """
            UPDATE history_attempts
            SET status = ?, finished_at = ?, duration_ms = ?, error_code = ?, error_message = ?
            WHERE history_id = ? AND status IN ('queued', 'running')
            """,
            (status, _encode_datetime(finished_at), duration_ms, error_code, error_message, history_id),
        )

    def list_history(
        self,
        query: str = "",
        status_filter: str = "all",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[HistoryAttempt]:
        clauses = ["status IN ('succeeded', 'failed', 'cancelled')"]
        values: list[object] = []
        if query:
            clauses.append("LOWER(input_path) LIKE LOWER(?)")
            values.append(f"%{query}%")
        if status_filter == "completed":
            clauses.append("status = 'succeeded'")
        elif status_filter == "unfinished":
            clauses.append("status IN ('failed', 'cancelled')")
        elif status_filter != "all":
            raise ValueError("unknown history status filter")
        if start_at:
            clauses.append("started_at >= ?")
            values.append(_encode_datetime(start_at))
        if end_at:
            clauses.append("started_at <= ?")
            values.append(_encode_datetime(end_at))
        rows = self._connection.execute(
            "SELECT * FROM history_attempts WHERE " + " AND ".join(clauses) + " ORDER BY started_at DESC",
            values,
        ).fetchall()
        return [_row_to_attempt(row) for row in rows]

    def delete_before(self, threshold: datetime) -> int:
        return self._execute_write(
            "DELETE FROM history_attempts WHERE started_at < ? AND status IN ('succeeded', 'failed', 'cancelled')",
            (_encode_datetime(threshold),),
        )

    def keep_latest(self, count: int) -> int:
        if count < 0:
            raise ValueError("count must not be negative")
        return self._execute_write(
            """
            DELETE FROM history_attempts
            WHERE history_id IN (
                SELECT history_id FROM history_attempts
                WHERE status IN ('succeeded', 'failed', 'cancelled')
                ORDER BY started_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (count,),
        )

    def clear(self) -> int:
        return self._execute_write(
            "DELETE FROM history_attempts WHERE status IN ('succeeded', 'failed', 'cancelled')"
        )

    def recover_active_attempts(self, finished_at: datetime) -> int:
        return self._execute_write(
            """
            UPDATE history_attempts
            SET status = 'failed', finished_at = ?, duration_ms = 0,
                error_code = 'app_interrupted', error_message = '应用意外关闭，转录未完成'
            WHERE status IN ('queued', 'running')
            """,
            (_encode_datetime(finished_at),),
        )

    def _open_or_rebuild(self) -> sqlite3.Connection:
        if self.path.exists() and not _is_valid_database(self.path):
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.path.with_name(f"history.corrupt-{suffix}.db")
            self.path.replace(backup)
        try:
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except sqlite3.DatabaseError as exc:
            raise HistoryStorageError("无法打开历史数据库") from exc

    def _migrate(self) -> None:
        with self._connection:
            self._connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self._connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] > SCHEMA_VERSION:
                raise HistoryStorageError("历史数据库版本高于当前应用")
            elif row[0] < SCHEMA_VERSION:
                self._connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_attempts (
                    history_id TEXT PRIMARY KEY NOT NULL,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE,
                    input_path TEXT NOT NULL,
                    midi_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
                    batch_size INTEGER NOT NULL CHECK (batch_size > 0),
                    queued_at TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    input_sha256 TEXT,
                    input_size_bytes INTEGER,
                    input_mtime_ns INTEGER,
                    input_duration_ms INTEGER,
                    input_sample_rate INTEGER,
                    input_channels INTEGER,
                    output_sha256 TEXT,
                    output_size_bytes INTEGER,
                    output_mtime_ns INTEGER,
                    app_version TEXT,
                    model_revision TEXT,
                    post_processor TEXT,
                    device_mode TEXT,
                    execution_device TEXT
                );
                CREATE INDEX IF NOT EXISTS history_attempts_started_at_idx
                    ON history_attempts(started_at DESC);
                CREATE INDEX IF NOT EXISTS history_attempts_status_started_at_idx
                    ON history_attempts(status, started_at DESC);
                CREATE INDEX IF NOT EXISTS history_attempts_input_path_idx
                    ON history_attempts(input_path);
                """
            )
            columns = {row[1] for row in self._connection.execute("PRAGMA table_info(history_attempts)")}
            additions = {
                "queued_at": "TEXT",
                "input_sha256": "TEXT",
                "input_size_bytes": "INTEGER",
                "input_mtime_ns": "INTEGER",
                "input_duration_ms": "INTEGER",
                "input_sample_rate": "INTEGER",
                "input_channels": "INTEGER",
                "output_sha256": "TEXT",
                "output_size_bytes": "INTEGER",
                "output_mtime_ns": "INTEGER",
                "app_version": "TEXT",
                "model_revision": "TEXT",
                "post_processor": "TEXT",
                "device_mode": "TEXT",
                "execution_device": "TEXT",
            }
            for name, sql_type in additions.items():
                if name not in columns:
                    self._connection.execute(f"ALTER TABLE history_attempts ADD COLUMN {name} {sql_type}")
            self._connection.execute("UPDATE history_attempts SET queued_at = started_at WHERE queued_at IS NULL")

    def _execute_write(self, query: str, values: tuple[object, ...] = ()) -> int:
        try:
            with self._connection:
                cursor = self._connection.execute(query, values)
            return cursor.rowcount
        except sqlite3.OperationalError as exc:
            raise HistoryStorageError("无法写入历史数据库") from exc


def _is_valid_database(path: Path) -> bool:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        if connection is not None:
            connection.close()


def _encode_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _row_to_attempt(row: sqlite3.Row) -> HistoryAttempt:
    return HistoryAttempt(
        history_id=row["history_id"],
        task_id=row["task_id"],
        attempt_id=row["attempt_id"],
        input_path=row["input_path"],
        midi_path=row["midi_path"],
        status=row["status"],
        batch_size=row["batch_size"],
        started_at=_decode_datetime(row["started_at"]),
        finished_at=_decode_datetime(row["finished_at"]) if row["finished_at"] else None,
        duration_ms=row["duration_ms"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        queued_at=_decode_datetime(row["queued_at"]) if row["queued_at"] else None,
        input_sha256=row["input_sha256"],
        input_size_bytes=row["input_size_bytes"],
        input_mtime_ns=row["input_mtime_ns"],
        input_duration_ms=row["input_duration_ms"],
        input_sample_rate=row["input_sample_rate"],
        input_channels=row["input_channels"],
        output_sha256=row["output_sha256"],
        output_size_bytes=row["output_size_bytes"],
        output_mtime_ns=row["output_mtime_ns"],
        app_version=row["app_version"],
        model_revision=row["model_revision"],
        post_processor=row["post_processor"],
        device_mode=row["device_mode"],
        execution_device=row["execution_device"],
    )
