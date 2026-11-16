"""Visible library operations and explicit recovery of lost successful output."""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactVerification, verify_file
from .product_store import ProductRepository, StaleRevision, _now


class RecoveryError(ValueError):
    pass


class LibraryService:
    def __init__(self, store: ProductRepository) -> None:
        self.store = store

    def inspect_successful_result(self, success_record_id: str) -> dict:
        record = self.store.get_success(success_record_id)
        if record is None:
            raise RecoveryError("successful result not found")
        checked = verify_file(Path(record["outputPath"]), record["outputSha256"])
        visible = []
        if self.store.get_attempt(record["sourceAttemptId"]):
            visible.append({"kind": "history", "id": record["sourceAttemptId"]})
        favorites = self.store.db.execute("""SELECT favorite_id FROM favorites
            WHERE source_attempt_id=?""", (record["sourceAttemptId"],)).fetchall()
        visible.extend({"kind": "favorite", "id": row["favorite_id"]} for row in favorites)
        return {"successRecordId": success_record_id, "revision": record["revision"],
                "outputPath": record["outputPath"], "outputStatus": checked.status.value,
                "visibleEntries": visible}

    def begin_result_recovery(self, success_record_id: str, expected_revision: int) -> str:
        record = self.store.get_success(success_record_id)
        if record is None or record["revision"] != expected_revision:
            raise StaleRevision(success_record_id)
        if verify_file(Path(record["outputPath"]), record["outputSha256"]).status == ArtifactVerification.MATCH:
            raise RecoveryError("registered MIDI is still available")
        if verify_file(Path(record["inputPath"]), record["inputSha256"]).status != ArtifactVerification.MATCH:
            raise RecoveryError("original audio must be relocated before recovery")
        token = str(uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.store.db:
            self.store.db.execute("""INSERT INTO recovery_tokens VALUES (?,?,?,?)
                ON CONFLICT(success_record_id) DO UPDATE SET token=excluded.token,
                revision=excluded.revision,expires_at=excluded.expires_at""",
                (token, success_record_id, expected_revision, expires))
        return token

    def create_recovery_work_item(self, token: str) -> dict:
        with self.store.db:
            issued = self.store.db.execute("SELECT * FROM recovery_tokens WHERE token=?", (token,)).fetchone()
            if issued is None or datetime.fromisoformat(issued["expires_at"]) < datetime.now(timezone.utc):
                raise RecoveryError("recovery authorization expired")
            record = self.store.get_success(issued["success_record_id"])
            if record is None or record["revision"] != issued["revision"]:
                raise StaleRevision("successful result changed")
            if verify_file(Path(record["outputPath"]), record["outputSha256"]).status == ArtifactVerification.MATCH:
                raise RecoveryError("registered MIDI is available again")
            if verify_file(Path(record["inputPath"]), record["inputSha256"]).status != ArtifactVerification.MATCH:
                raise RecoveryError("original audio changed")
            order = self.store.db.execute("SELECT coalesce(max(order_key),0)+1 FROM work_items").fetchone()[0]
            task_id = str(uuid4())
            try:
                self.store.db.execute("""INSERT INTO work_items
                    (task_id,input_path,output_rule,output_dir,midi_name,batch_size,order_key,checked,
                     recovery_record_id,revision,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,0,?)""",
                    (task_id, record["inputPath"], "explicit_directory", str(Path(record["outputPath"]).parent),
                     Path(record["outputPath"]).name, 16, order, 1, record["successRecordId"], _now()))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("audio or recovery item already in workbench") from exc
            self.store.db.execute("DELETE FROM recovery_tokens WHERE token=?", (token,))
            return self.store.get_work_item(task_id)

    def relocate_success_input(self, success_record_id: str, path: Path, expected_revision: int) -> dict:
        record = self.store.get_success(success_record_id)
        if record is None or record["revision"] != expected_revision:
            raise StaleRevision(success_record_id)
        checked = verify_file(path, record["inputSha256"])
        if checked.status != ArtifactVerification.MATCH:
            raise RecoveryError("selected audio does not match registered input")
        with self.store.db:
            result = self.store.db.execute("""UPDATE successful_inputs SET input_path=?,revision=revision+1
                WHERE success_record_id=? AND revision=?""", (str(path.resolve()), success_record_id, expected_revision))
            if result.rowcount != 1:
                raise StaleRevision(success_record_id)
        return self.store.get_success(success_record_id)

    def preview_requeue(self, attempt_ids: list[str]) -> dict:
        eligible, ineligible, scope = [], [], []
        with self.store.db:
            for attempt_id in dict.fromkeys(attempt_ids):
                row = self.store.get_attempt(attempt_id)
                if row and row["status"] in ("failed", "cancelled"):
                    eligible.append(attempt_id)
                    scope.append([attempt_id, row["revision"]])
                else:
                    ineligible.append(attempt_id)
            token = str(uuid4())
            expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            self.store.db.execute("INSERT INTO batch_confirmations VALUES (?,?,?,?)",
                                  (token, "requeue", json.dumps(scope), expires))
        return {"token": token, "eligible": eligible, "ineligible": ineligible}

    def requeue_eligible(self, token: str, attempt_ids: list[str]) -> list[dict]:
        with self.store.db:
            preview = self.store.db.execute("SELECT * FROM batch_confirmations WHERE token=? AND action='requeue'",
                                            (token,)).fetchone()
            if preview is None or datetime.fromisoformat(preview["expires_at"]) < datetime.now(timezone.utc):
                raise RecoveryError("requeue confirmation expired")
            scope = dict(json.loads(preview["scope_json"]))
            if len(attempt_ids) != len(set(attempt_ids)) or not set(attempt_ids) <= set(scope):
                raise RecoveryError("IDs exceed confirmed scope")
            order = self.store.db.execute("SELECT coalesce(max(order_key),0) FROM work_items").fetchone()[0]
            created = []
            for attempt_id in attempt_ids:
                row = self.store.get_attempt(attempt_id)
                if row is None or row["revision"] != scope[attempt_id] or row["status"] not in ("failed", "cancelled"):
                    raise StaleRevision(attempt_id)
                order += 1
                task_id = str(uuid4())
                self.store.db.execute("""INSERT INTO work_items
                    (task_id,input_path,output_rule,output_dir,midi_name,batch_size,order_key,checked,revision,created_at)
                    VALUES (?,?,?,?,?,?,?,?,0,?)""",
                    (task_id, row["input_path"], "explicit_directory", str(Path(row["midi_path"]).parent),
                     Path(row["midi_path"]).name, row["batch_size"], order, 1, _now()))
                created.append(self.store.get_work_item(task_id))
            self.store.db.execute("DELETE FROM batch_confirmations WHERE token=?", (token,))
        return created
