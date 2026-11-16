"""Persistent product state, separate from the development history database."""

from __future__ import annotations

import os
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactSnapshotError, snapshot_file


SCHEMA_VERSION = 2
SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


class StaleRevision(ValueError):
    pass


def default_product_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "BetterPianoTrans" / "product-v1.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class ProductRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_product_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def _initialize(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, SCHEMA_VERSION):
            raise RuntimeError(f"Unsupported product database version: {version}")
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS work_items (
                    task_id TEXT PRIMARY KEY,
                    input_path TEXT NOT NULL UNIQUE,
                    output_rule TEXT NOT NULL CHECK(output_rule IN ('source_directory','explicit_directory')),
                    output_dir TEXT,
                    midi_name TEXT NOT NULL,
                    batch_size INTEGER NOT NULL CHECK(batch_size > 0),
                    order_key INTEGER NOT NULL,
                    checked INTEGER NOT NULL CHECK(checked IN (0,1)),
                    recovery_record_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS work_order ON work_items(order_key, task_id);
                CREATE TABLE IF NOT EXISTS history_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    submission_id TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    midi_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
                    input_size INTEGER NOT NULL CHECK(input_size >= 0),
                    input_mtime INTEGER NOT NULL,
                    batch_size INTEGER NOT NULL DEFAULT 16 CHECK(batch_size > 0),
                    device_mode TEXT NOT NULL DEFAULT 'auto' CHECK(device_mode IN ('auto','cpu','cuda')),
                    queue_order INTEGER NOT NULL DEFAULT 0,
                    recovery_record_id TEXT,
                    output_sha256 TEXT,
                    output_size INTEGER,
                    output_mtime INTEGER,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    CHECK(status != 'succeeded' OR
                        (output_sha256 IS NOT NULL AND length(output_sha256)=64
                         AND output_size IS NOT NULL AND output_mtime IS NOT NULL))
                );
                CREATE INDEX IF NOT EXISTS history_order ON history_attempts(queued_at DESC, attempt_id DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS active_input ON history_attempts(input_sha256)
                    WHERE status IN ('queued','running');
                CREATE UNIQUE INDEX IF NOT EXISTS active_target ON history_attempts(midi_path)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS successful_inputs (
                    success_record_id TEXT PRIMARY KEY,
                    input_sha256 TEXT NOT NULL UNIQUE CHECK(length(input_sha256)=64),
                    source_attempt_id TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    input_size INTEGER NOT NULL,
                    input_mtime INTEGER NOT NULL,
                    output_path TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL CHECK(length(output_sha256)=64),
                    output_size INTEGER NOT NULL,
                    output_mtime INTEGER NOT NULL,
                    finished_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS successful_output_hash ON successful_inputs(output_sha256);
                CREATE TABLE IF NOT EXISTS favorites (
                    favorite_id TEXT PRIMARY KEY,
                    source_attempt_id TEXT,
                    input_path TEXT NOT NULL,
                    midi_path TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
                    input_size INTEGER NOT NULL,
                    input_mtime INTEGER NOT NULL,
                    output_sha256 TEXT NOT NULL CHECK(length(output_sha256)=64),
                    output_size INTEGER NOT NULL,
                    output_mtime INTEGER NOT NULL,
                    transcription_finished_at TEXT NOT NULL,
                    favorited_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS favorite_source ON favorites(source_attempt_id)
                    WHERE source_attempt_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS collections (
                    collection_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS collection_items (
                    favorite_id TEXT NOT NULL REFERENCES favorites(favorite_id) ON DELETE CASCADE,
                    collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
                    PRIMARY KEY(favorite_id, collection_id)
                );
                CREATE TABLE IF NOT EXISTS product_revision (
                    id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO product_revision VALUES (1,0);
                CREATE TABLE IF NOT EXISTS command_results (
                    request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_confirmations (
                    token TEXT PRIMARY KEY, action TEXT NOT NULL, scope_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_tokens (
                    token TEXT PRIMARY KEY, success_record_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)
            if version == 1:
                required = {
                    "work_items": {"recovery_record_id": "TEXT"},
                    "history_attempts": {"recovery_record_id": "TEXT", "batch_size": "INTEGER NOT NULL DEFAULT 16",
                                         "device_mode": "TEXT NOT NULL DEFAULT 'auto'",
                                         "queue_order": "INTEGER NOT NULL DEFAULT 0"},
                }
                for table, additions in required.items():
                    columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
                    for name, declaration in additions.items():
                        if name not in columns:
                            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            self.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS pending_recovery ON work_items(recovery_record_id)
                WHERE recovery_record_id IS NOT NULL""")
            self.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS active_recovery ON history_attempts(recovery_record_id)
                WHERE recovery_record_id IS NOT NULL AND status IN ('queued','running')""")
            for table in ("work_items", "history_attempts", "successful_inputs", "favorites",
                          "collections", "collection_items"):
                for action in ("INSERT", "UPDATE", "DELETE"):
                    self.db.execute(f"""CREATE TRIGGER IF NOT EXISTS revision_{table}_{action.lower()}
                        AFTER {action} ON {table} BEGIN
                        UPDATE product_revision SET value=value+1 WHERE id=1;
                        END""")
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def query_token(self) -> int:
        return self.db.execute("SELECT value FROM product_revision WHERE id=1").fetchone()[0]

    def get_setting(self, key: str, default: str) -> str:
        row = self.db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO app_settings(key,value) VALUES (?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))

    def list_collections(self) -> list[dict]:
        rows = self.db.execute("""SELECT collection_id,name,revision,
            (SELECT count(*) FROM collection_items ci WHERE ci.collection_id=c.collection_id) AS item_count
            FROM collections c ORDER BY name COLLATE NOCASE,collection_id""").fetchall()
        return [dict(row) for row in rows]

    def import_audio(self, paths: list[Path], *, request_id: str | None = None) -> dict:
        payload = json.dumps([str(Path(path).resolve(strict=False)) for path in paths], ensure_ascii=False)
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        imported, skipped = [], []
        pending_hashes = set()
        for row in self.db.execute("SELECT input_path FROM work_items"):
            try:
                pending_hashes.add(snapshot_file(Path(row["input_path"])).sha256)
            except (OSError, ValueError, ArtifactSnapshotError):
                continue
        with self.db:
            if request_id:
                prior = self.db.execute("SELECT * FROM command_results WHERE request_id=?", (request_id,)).fetchone()
                if prior:
                    if prior["payload_sha256"] != payload_hash:
                        raise ValueError("request ID reused with different paths")
                    return json.loads(prior["result_json"])
            next_order = self.db.execute("SELECT coalesce(max(order_key),0) FROM work_items").fetchone()[0]
            for candidate in paths:
                path = Path(candidate).resolve(strict=False)
                reason = None
                if path.suffix.lower() not in SUPPORTED_AUDIO:
                    reason = "unsupported_format"
                elif not path.is_file():
                    reason = "input_missing"
                elif not os.access(path, os.R_OK):
                    reason = "unreadable_input"
                elif self.db.execute("SELECT 1 FROM work_items WHERE input_path=?", (str(path),)).fetchone():
                    reason = "already_in_workbench"
                if reason is None:
                    try:
                        input_hash = snapshot_file(path).sha256
                    except (OSError, ValueError, ArtifactSnapshotError):
                        reason = "fingerprint_failed"
                    else:
                        if self.find_success(input_hash):
                            reason = "already_transcribed"
                        elif self.db.execute("""SELECT 1 FROM history_attempts
                                WHERE input_sha256=? AND status IN ('queued','running')""",
                                             (input_hash,)).fetchone():
                            reason = "already_active"
                        elif input_hash in pending_hashes:
                            reason = "duplicate_content"
                if reason:
                    skipped.append({"path": str(path), "code": reason})
                    continue
                next_order += 1
                task_id = str(uuid4())
                self.db.execute("""INSERT INTO work_items
                    (task_id,input_path,output_rule,output_dir,midi_name,batch_size,order_key,checked,revision,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                    task_id, str(path), "source_directory", None, path.stem + ".mid",
                    16, next_order, 1, 0, _now()))
                imported.append(self.get_work_item(task_id))
                pending_hashes.add(input_hash)
            result = {"imported": imported, "skipped": skipped}
            if request_id:
                self.db.execute("INSERT INTO command_results VALUES (?,?,?)",
                                (request_id, payload_hash, json.dumps(result, ensure_ascii=False)))
        return result

    def get_work_item(self, task_id: str) -> dict | None:
        row = _row(self.db.execute("SELECT * FROM work_items WHERE task_id=?", (task_id,)).fetchone())
        return self._work_projection(row) if row else None

    @staticmethod
    def _work_projection(row: dict) -> dict:
        return {"taskId": row["task_id"], "inputPath": row["input_path"],
                "outputDirectoryRule": row["output_rule"], "explicitOutputDirectory": row["output_dir"],
                "midiFileName": row["midi_name"], "batchSize": row["batch_size"],
                "orderKey": row["order_key"], "checked": bool(row["checked"]),
                "revision": row["revision"], "recoveryRecordId": row["recovery_record_id"]}

    def list_work_items(self, *, limit: int = 100, after: tuple[int, str] | None = None) -> list[dict]:
        if not 1 <= limit <= 501:
            raise ValueError("limit must be between 1 and 501")
        if after:
            rows = self.db.execute("""SELECT * FROM work_items
                WHERE (order_key, task_id) > (?, ?) ORDER BY order_key, task_id LIMIT ?""",
                (*after, limit)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM work_items ORDER BY order_key, task_id LIMIT ?", (limit,)).fetchall()
        return [self._work_projection(dict(row)) for row in rows]

    def page_work_items(self, *, limit: int = 100, after: tuple[int, str] | None = None,
                        query_token: int | None = None) -> dict:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        current = self.query_token()
        if query_token is not None and current != query_token:
            raise StaleRevision("work item query changed")
        items = self.list_work_items(limit=limit + 1, after=after)
        more = len(items) > limit
        items = items[:limit]
        cursor = (items[-1]["orderKey"], items[-1]["taskId"]) if more else None
        return {"items": items, "nextCursor": cursor, "queryToken": current,
                "totalCount": self.db.execute("SELECT count(*) FROM work_items").fetchone()[0]}

    def numbered_work_page(self, page: int, *, limit: int = 20) -> dict:
        if page < 1 or limit != 20:
            raise ValueError("invalid work page")
        total = self.db.execute("SELECT count(*) FROM work_items").fetchone()[0]
        page_count = max(1, (total + limit - 1) // limit)
        page = min(page, page_count)
        rows = self.db.execute("""SELECT * FROM work_items ORDER BY order_key,task_id
            LIMIT ? OFFSET ?""", (limit, (page - 1) * limit)).fetchall()
        return {"items": [self._work_projection(dict(row)) for row in rows],
                "totalCount": total, "page": page, "pageCount": page_count,
                "pageSize": limit, "queryToken": self.query_token(),
                "checkedCount": self.db.execute("SELECT count(*) FROM work_items WHERE checked=1").fetchone()[0]}

    def set_checked(self, ids: list[str], checked: bool) -> int:
        with self.db:
            return sum(self.db.execute("UPDATE work_items SET checked=?, revision=revision+1 WHERE task_id=?",
                (int(checked), task_id)).rowcount for task_id in set(ids))

    def set_all_checked(self, checked: bool) -> int:
        with self.db:
            result = self.db.execute("UPDATE work_items SET checked=?,revision=revision+1 WHERE checked!=?",
                                     (int(checked), int(checked)))
            return result.rowcount

    def checked_work_revisions(self) -> dict[str, int]:
        rows = self.db.execute("SELECT task_id,revision FROM work_items WHERE checked=1 ORDER BY order_key,task_id")
        return {row["task_id"]: row["revision"] for row in rows}

    def set_checked_config(self, patch: dict, query_token: int) -> int:
        allowed = {"batchSize": "batch_size", "outputDirectoryRule": "output_rule",
                   "explicitOutputDirectory": "output_dir"}
        if not patch or set(patch) - set(allowed):
            raise ValueError("invalid batch config")
        if "batchSize" in patch and (type(patch["batchSize"]) is not int or patch["batchSize"] < 1):
            raise ValueError("invalid batch size")
        if "outputDirectoryRule" in patch and patch["outputDirectoryRule"] not in ("source_directory", "explicit_directory"):
            raise ValueError("invalid output rule")
        if patch.get("outputDirectoryRule") == "explicit_directory" and not patch.get("explicitOutputDirectory"):
            raise ValueError("explicit output directory required")
        if self.query_token() != query_token:
            raise StaleRevision("workbench changed after batch dialog opened")
        columns = ", ".join(f"{allowed[key]}=?" for key in patch)
        with self.db:
            result = self.db.execute(f"UPDATE work_items SET {columns},revision=revision+1 WHERE checked=1",
                                     tuple(patch.values()))
            return result.rowcount

    def remove_work_items(self, ids: list[str]) -> int:
        with self.db:
            return sum(self.db.execute("DELETE FROM work_items WHERE task_id=?", (task_id,)).rowcount
                       for task_id in set(ids))

    def remove_checked_work_items(self, *, page_ids: list[str] | None = None,
                                  query_token: int | None = None) -> int:
        with self.db:
            if query_token is not None and self.query_token() != query_token:
                raise StaleRevision("work page changed")
            if page_ids is None:
                return self.db.execute("DELETE FROM work_items WHERE checked=1").rowcount
            ids = list(dict.fromkeys(page_ids))
            if not ids:
                return 0
            marks = ",".join("?" for _ in ids)
            return self.db.execute(f"DELETE FROM work_items WHERE checked=1 AND task_id IN ({marks})", ids).rowcount

    def reassign_input(self, task_id: str, path: Path, revision: int) -> dict:
        candidate = Path(path).resolve(strict=False)
        if candidate.suffix.lower() not in SUPPORTED_AUDIO or not candidate.is_file() or not os.access(candidate, os.R_OK):
            raise ValueError("选择可读取的受支持音频")
        with self.db:
            result = self.db.execute("""UPDATE work_items SET input_path=?,revision=revision+1
                WHERE task_id=? AND revision=?""", (str(candidate), task_id, revision))
            if result.rowcount != 1:
                raise StaleRevision(task_id)
        return self.get_work_item(task_id)

    def set_config(self, task_id: str, patch: dict, revision: int) -> dict:
        allowed = {"batchSize": "batch_size", "midiFileName": "midi_name",
                   "outputDirectoryRule": "output_rule", "explicitOutputDirectory": "output_dir"}
        if not patch or set(patch) - set(allowed):
            raise ValueError("invalid config patch")
        if "batchSize" in patch and (type(patch["batchSize"]) is not int or patch["batchSize"] < 1):
            raise ValueError("invalid batch size")
        if "midiFileName" in patch:
            name = patch["midiFileName"]
            if not isinstance(name, str) or not name or any(c in name for c in '<>:"/\\|?*') or name in {".", ".."}:
                raise ValueError("invalid MIDI name")
            if not name.lower().endswith(".mid"):
                patch = {**patch, "midiFileName": name + ".mid"}
        if "outputDirectoryRule" in patch and patch["outputDirectoryRule"] not in ("source_directory", "explicit_directory"):
            raise ValueError("invalid output rule")
        if "explicitOutputDirectory" in patch and patch["explicitOutputDirectory"] is not None:
            directory = patch["explicitOutputDirectory"]
            if not isinstance(directory, str) or not directory.strip():
                raise ValueError("invalid output directory")
        current = self.get_work_item(task_id)
        if current is None or current["revision"] != revision:
            raise StaleRevision(task_id)
        rule = patch.get("outputDirectoryRule", current["outputDirectoryRule"])
        directory = patch.get("explicitOutputDirectory", current["explicitOutputDirectory"])
        if rule == "explicit_directory" and not directory:
            raise ValueError("explicit output directory required")
        with self.db:
            fields = ", ".join(f"{allowed[key]}=?" for key in patch)
            result = self.db.execute(f"UPDATE work_items SET {fields}, revision=revision+1 WHERE task_id=? AND revision=?",
                (*patch.values(), task_id, revision))
            if result.rowcount != 1:
                raise StaleRevision(task_id)
        return self.get_work_item(task_id)

    def set_work_item_order(self, ids: list[str]) -> None:
        with self.db:
            actual = {row[0] for row in self.db.execute("SELECT task_id FROM work_items")}
            if len(ids) != len(actual) or set(ids) != actual:
                raise ValueError("order must include each current work item exactly once")
            self.db.executemany("UPDATE work_items SET order_key=?, revision=revision+1 WHERE task_id=?",
                [(index, task_id) for index, task_id in enumerate(ids, 1)])

    def move_work_item(self, source_id: str, target_id: str) -> None:
        ids = [row[0] for row in self.db.execute("SELECT task_id FROM work_items ORDER BY order_key,task_id")]
        if source_id not in ids or target_id not in ids:
            raise StaleRevision("work item changed")
        ids.remove(source_id)
        ids.insert(ids.index(target_id), source_id)
        self.set_work_item_order(ids)

    def create_attempt(self, attempt_id: str, task_id: str, submission_id: str, input_path: str,
                       midi_path: str, input_sha256: str | None, input_size: int,
                       input_mtime: int, batch_size: int = 16, device_mode: str = "auto",
                       queue_order: int = 0) -> None:
        with self.db:
            self.db.execute("""INSERT INTO history_attempts
                (attempt_id,task_id,submission_id,input_path,midi_path,status,input_sha256,input_size,input_mtime,
                 batch_size,device_mode,queue_order,queued_at)
                VALUES (?,?,?,?,?,'queued',?,?,?,?,?,?,?)""",
                (attempt_id, task_id, submission_id, input_path, midi_path, input_sha256,
                 input_size, input_mtime, batch_size, device_mode, queue_order, _now()))

    def enqueue_prepared(self, submission_id: str, items: list[dict], device_mode: str) -> list[str]:
        if self.db.in_transaction:
            raise RuntimeError("another transaction is active")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            order = self.db.execute("SELECT coalesce(max(queue_order),0) FROM history_attempts").fetchone()[0]
            attempt_ids = []
            for item in items:
                work = self.get_work_item(item["taskId"])
                if work is None or work["revision"] != item["revision"] or not work["checked"]:
                    raise StaleRevision(item["taskId"])
                success = self.find_success(item["snapshot"].sha256)
                if success and (work["recoveryRecordId"] != success["successRecordId"] or
                                item.get("recoveryRecordId") != success["successRecordId"]):
                    raise StaleRevision("input already succeeded")
                attempt_id = str(uuid4())
                order += 1
                snapshot = item["snapshot"]
                self.db.execute("""INSERT INTO history_attempts
                    (attempt_id,task_id,submission_id,input_path,midi_path,status,input_sha256,input_size,input_mtime,
                     batch_size,device_mode,queue_order,recovery_record_id,queued_at)
                    VALUES (?,?,?,?,?,'queued',?,?,?,?,?,?,?,?)""",
                    (attempt_id, item["taskId"], submission_id, work["inputPath"], item["midiPath"],
                     snapshot.sha256, snapshot.size_bytes, snapshot.mtime_ns, work["batchSize"],
                     device_mode, order, work["recoveryRecordId"], _now()))
                self.db.execute("DELETE FROM work_items WHERE task_id=?", (item["taskId"],))
                attempt_ids.append(attempt_id)
            self.db.commit()
            return attempt_ids
        except BaseException:
            self.db.rollback()
            raise

    def mark_running(self, attempt_id: str) -> None:
        with self.db:
            result = self.db.execute("""UPDATE history_attempts SET status='running',started_at=?,revision=revision+1
                WHERE attempt_id=? AND status='queued'""", (_now(), attempt_id))
            if result.rowcount != 1:
                raise StaleRevision(attempt_id)

    def reorder_queued(self, ids: list[str], revisions: dict[str, int]) -> None:
        with self.db:
            rows = self.db.execute("SELECT attempt_id,revision FROM history_attempts WHERE status='queued'").fetchall()
            current = {row["attempt_id"]: row["revision"] for row in rows}
            if len(ids) != len(current) or set(ids) != set(current):
                raise ValueError("order must include every waiting attempt exactly once")
            if any(revisions.get(attempt_id) != revision for attempt_id, revision in current.items()):
                raise StaleRevision("queued order changed")
            self.db.executemany("""UPDATE history_attempts SET queue_order=?,revision=revision+1
                WHERE attempt_id=?""", [(index, attempt_id) for index, attempt_id in enumerate(ids, 1)])

    def recover_incomplete_attempts(self) -> int:
        with self.db:
            result = self.db.execute("""UPDATE history_attempts SET status='failed',finished_at=?,
                error_code='app_interrupted',error_message='应用意外关闭，转录未完成',revision=revision+1
                WHERE status IN ('queued','running')""", (_now(),))
            return result.rowcount

    def get_attempt(self, attempt_id: str) -> dict | None:
        return _row(self.db.execute("SELECT * FROM history_attempts WHERE attempt_id=?", (attempt_id,)).fetchone())

    def list_history(self, *, status: str = "all", query: str = "", limit: int = 100,
                     after: tuple[str, str] | None = None, query_token: int | None = None) -> dict:
        if status not in ("all", "queued", "running", "succeeded", "failed", "cancelled") or not 1 <= limit <= 500:
            raise ValueError("invalid page request")
        token = self.query_token()
        if query_token is not None and query_token != token:
            raise StaleRevision("history query changed")
        conditions, params = [], []
        if status != "all":
            conditions.append("status=?")
            params.append(status)
        if query:
            conditions.append("input_path LIKE ? ESCAPE '\\'")
            params.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        count_where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = self.db.execute(f"SELECT count(*) FROM history_attempts{count_where}", params).fetchone()[0]
        if after:
            conditions.append("(queued_at,attempt_id) < (?,?)")
            params.extend(after)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.db.execute(f"""SELECT h.*, (SELECT f.favorite_id FROM favorites f
            WHERE f.source_attempt_id=h.attempt_id) AS favorite_id
            FROM history_attempts h{where} ORDER BY queued_at DESC,attempt_id DESC LIMIT ?""",
                               (*params, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        cursor = (items[-1]["queued_at"], items[-1]["attempt_id"]) if len(rows) > limit else None
        return {"items": items, "nextCursor": cursor, "totalCount": total, "queryToken": token}

    def finish_attempt(self, attempt_id: str, status: str, *, output_sha256: str | None = None,
                       output_size: int | None = None, output_mtime: int | None = None,
                       error_code: str | None = None, error_message: str | None = None) -> None:
        if status not in ("succeeded", "failed", "cancelled"):
            raise ValueError("invalid terminal status")
        with self.db:
            result = self.db.execute("""UPDATE history_attempts SET status=?, output_sha256=?,
                output_size=?, output_mtime=?, error_code=?, error_message=?, finished_at=?, revision=revision+1
                WHERE attempt_id=? AND status IN ('queued','running')""",
                (status, output_sha256, output_size, output_mtime, error_code, error_message, _now(), attempt_id))
            if result.rowcount != 1:
                raise StaleRevision(attempt_id)
            if status == "succeeded":
                row = self.get_attempt(attempt_id)
                if row["recovery_record_id"]:
                    updated = self.db.execute("""UPDATE successful_inputs SET source_attempt_id=?,input_path=?,
                        input_size=?,input_mtime=?,output_path=?,output_sha256=?,output_size=?,output_mtime=?,
                        finished_at=?,revision=revision+1 WHERE success_record_id=? AND input_sha256=?""",
                        (attempt_id, row["input_path"], row["input_size"], row["input_mtime"], row["midi_path"],
                         row["output_sha256"], row["output_size"], row["output_mtime"], row["finished_at"],
                         row["recovery_record_id"], row["input_sha256"]))
                    if updated.rowcount != 1:
                        raise StaleRevision("recovery record changed")
                else:
                    self.db.execute("""INSERT INTO successful_inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                        (str(uuid4()), row["input_sha256"], attempt_id, row["input_path"],
                         row["input_size"], row["input_mtime"], row["midi_path"],
                         row["output_sha256"], row["output_size"], row["output_mtime"], row["finished_at"]))

    def find_success(self, input_sha256: str) -> dict | None:
        row = _row(self.db.execute("SELECT * FROM successful_inputs WHERE input_sha256=?", (input_sha256,)).fetchone())
        return self._success_projection(row)

    def find_successes_by_output_hash(self, output_sha256: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM successful_inputs WHERE output_sha256=?",
                               (output_sha256,)).fetchall()
        return [self._success_projection(dict(row)) for row in rows]

    def get_success(self, success_record_id: str) -> dict | None:
        row = _row(self.db.execute("SELECT * FROM successful_inputs WHERE success_record_id=?",
                                   (success_record_id,)).fetchone())
        return self._success_projection(row)

    @staticmethod
    def _success_projection(row: dict | None) -> dict | None:
        return ({"successRecordId": row["success_record_id"], "inputSha256": row["input_sha256"],
                 "sourceAttemptId": row["source_attempt_id"], "inputPath": row["input_path"],
                 "inputSize": row["input_size"], "inputMtime": row["input_mtime"],
                 "outputPath": row["output_path"], "outputSha256": row["output_sha256"],
                 "outputSize": row["output_size"], "outputMtime": row["output_mtime"],
                 "revision": row["revision"]} if row else None)

    def delete_history(self, ids: list[str]) -> int:
        with self.db:
            return sum(self.db.execute("""DELETE FROM history_attempts WHERE attempt_id=?
                AND status IN ('succeeded','failed','cancelled')""", (item,)).rowcount for item in set(ids))

    def preview_delete_history(self, ids: list[str] | None = None) -> dict:
        with self.db:
            if ids is None:
                rows = self.db.execute("""SELECT attempt_id,revision FROM history_attempts
                    WHERE status IN ('succeeded','failed','cancelled')""").fetchall()
            else:
                rows = [row for attempt_id in set(ids) if (row := self.db.execute(
                    """SELECT attempt_id,revision FROM history_attempts WHERE attempt_id=?
                    AND status IN ('succeeded','failed','cancelled')""", (attempt_id,)).fetchone())]
            scope = [[row["attempt_id"], row["revision"]] for row in rows]
            token = str(uuid4())
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            self.db.execute("INSERT INTO batch_confirmations VALUES (?,?,?,?)",
                            (token, "delete_history", json.dumps(scope), expires_at))
        return {"token": token, "ids": [entry[0] for entry in scope], "count": len(scope)}

    def confirm_delete_history(self, token: str, ids: list[str]) -> dict:
        with self.db:
            preview = self.db.execute("SELECT * FROM batch_confirmations WHERE token=? AND action='delete_history'",
                                      (token,)).fetchone()
            if preview is None or datetime.fromisoformat(preview["expires_at"]) < datetime.now(timezone.utc):
                raise StaleRevision("confirmation expired")
            scope = dict(json.loads(preview["scope_json"]))
            if len(ids) != len(set(ids)) or not set(ids) <= set(scope):
                raise ValueError("IDs exceed confirmed scope")
            deleted, changed = [], []
            for attempt_id in ids:
                result = self.db.execute("""DELETE FROM history_attempts WHERE attempt_id=? AND revision=?
                    AND status IN ('succeeded','failed','cancelled')""",
                                         (attempt_id, scope[attempt_id]))
                (deleted if result.rowcount else changed).append(attempt_id)
            self.db.execute("DELETE FROM batch_confirmations WHERE token=?", (token,))
        return {"deleted": deleted, "changed": changed}

    def add_favorite(self, attempt_id: str) -> dict:
        with self.db:
            row = self.get_attempt(attempt_id)
            if row is None or row["status"] != "succeeded":
                raise ValueError("only successful attempts may be favorited")
            existing = self.db.execute("SELECT * FROM favorites WHERE source_attempt_id=?", (attempt_id,)).fetchone()
            if existing:
                return dict(existing)
            favorite_id = str(uuid4())
            self.db.execute("""INSERT INTO favorites
                (favorite_id,source_attempt_id,input_path,midi_path,input_sha256,input_size,input_mtime,
                 output_sha256,output_size,output_mtime,transcription_finished_at,favorited_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (favorite_id, attempt_id, row["input_path"], row["midi_path"], row["input_sha256"],
                 row["input_size"], row["input_mtime"], row["output_sha256"], row["output_size"],
                 row["output_mtime"], row["finished_at"], _now()))
            return {"favoriteId": favorite_id}

    def get_favorite(self, favorite_id: str) -> dict | None:
        return _row(self.db.execute("SELECT * FROM favorites WHERE favorite_id=?", (favorite_id,)).fetchone())

    def list_favorites(self, *, sort: str = "favorited", collection: str = "all", query: str = "",
                       limit: int = 100, after: tuple[str, str] | None = None,
                       query_token: int | None = None) -> dict:
        if sort not in ("favorited", "transcribed") or not 1 <= limit <= 500:
            raise ValueError("invalid page request")
        token = self.query_token()
        if query_token is not None and query_token != token:
            raise StaleRevision("favorites query changed")
        column = "favorited_at" if sort == "favorited" else "transcription_finished_at"
        where, params = [], []
        if collection == "unclassified":
            where.append("NOT EXISTS (SELECT 1 FROM collection_items ci WHERE ci.favorite_id=f.favorite_id)")
        elif collection != "all":
            where.append("EXISTS (SELECT 1 FROM collection_items ci WHERE ci.favorite_id=f.favorite_id AND ci.collection_id=?)")
            params.append(collection)
        if query:
            where.append("f.input_path LIKE ? ESCAPE '\\'")
            params.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        count_clause = " WHERE " + " AND ".join(where) if where else ""
        total = self.db.execute(f"SELECT count(*) FROM favorites f{count_clause}", params).fetchone()[0]
        if after:
            where.append(f"(f.{column},f.favorite_id) < (?,?)")
            params.extend(after)
        clause = " WHERE " + " AND ".join(where) if where else ""
        rows = self.db.execute(f"SELECT f.* FROM favorites f{clause} ORDER BY f.{column} DESC,f.favorite_id DESC LIMIT ?",
                               (*params, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        for item in items:
            item["favoriteId"] = item["favorite_id"]
            memberships = self.db.execute("SELECT collection_id FROM collection_items WHERE favorite_id=?",
                                          (item["favorite_id"],)).fetchall()
            item["collectionIds"] = [row[0] for row in memberships]
        cursor = (items[-1][column], items[-1]["favorite_id"]) if len(rows) > limit else None
        return {"items": items, "nextCursor": cursor, "totalCount": total, "queryToken": token}

    def remove_favorites(self, ids: list[str]) -> int:
        with self.db:
            return sum(self.db.execute("DELETE FROM favorites WHERE favorite_id=?", (item,)).rowcount for item in set(ids))

    def create_collection(self, name: str) -> dict:
        if not name.strip():
            raise ValueError("collection name required")
        collection_id = str(uuid4())
        with self.db:
            self.db.execute("INSERT INTO collections VALUES (?,?,?,0)", (collection_id, name.strip(), _now()))
        return {"collectionId": collection_id, "name": name.strip()}

    def delete_collection(self, collection_id: str) -> int:
        with self.db:
            return self.db.execute("DELETE FROM collections WHERE collection_id=?", (collection_id,)).rowcount

    def rename_collection(self, collection_id: str, name: str, revision: int) -> dict:
        if not name.strip():
            raise ValueError("collection name required")
        with self.db:
            result = self.db.execute("""UPDATE collections SET name=?,revision=revision+1
                WHERE collection_id=? AND revision=?""", (name.strip(), collection_id, revision))
            if result.rowcount != 1:
                raise StaleRevision(collection_id)
        return {"collectionId": collection_id, "name": name.strip(), "revision": revision + 1}

    def set_membership_delta(self, favorite_ids: list[str], *, add: list[str] = (), remove: list[str] = ()) -> None:
        if set(add) & set(remove):
            raise ValueError("contradictory membership delta")
        with self.db:
            for favorite_id in set(favorite_ids):
                for collection_id in add:
                    self.db.execute("INSERT OR IGNORE INTO collection_items VALUES (?,?)", (favorite_id, collection_id))
                for collection_id in remove:
                    self.db.execute("DELETE FROM collection_items WHERE favorite_id=? AND collection_id=?",
                                    (favorite_id, collection_id))
