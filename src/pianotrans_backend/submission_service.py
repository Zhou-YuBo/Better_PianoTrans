"""Submission and single-worker coordination for the product database."""

from __future__ import annotations

import os
import json
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactSnapshotError, FileSnapshot, snapshot_file, verify_file
from .devices import resolve_device, validate_device_mode
from .model import Task, TaskStatus, utc_now
from .product_store import ProductRepository, StaleRevision
from .scheduler import WorkerHandle, WorkerStarter


class SubmissionService:
    def __init__(self, store: ProductRepository, starter: WorkerStarter) -> None:
        self.store = store
        self.starter = starter
        self.recovered = store.recover_incomplete_attempts()
        self._sessions: dict[str, dict] = {}
        self._pending: list[str] = []
        self._current: str | None = None
        self._handle: WorkerHandle | None = None
        self._stopping = False

    def prepare_submission(self, task_revisions: dict[str, int], *, device_mode: str = "auto") -> dict:
        validate_device_mode(device_mode)
        if device_mode == "cuda":
            resolve_device(device_mode)
        if not task_revisions:
            raise ValueError("nothing selected")
        work_items = []
        for task_id, revision in task_revisions.items():
            work = self.store.get_work_item(task_id)
            if work is None or work["revision"] != revision or not work["checked"]:
                raise StaleRevision(task_id)
            work_items.append(work)
        work_items.sort(key=lambda item: (item["orderKey"], item["taskId"]))
        seen_hashes: set[str] = set()
        seen_targets: set[str] = set()
        prepared = []
        for work in work_items:
            input_path = Path(work["inputPath"])
            row = {"taskId": work["taskId"], "revision": work["revision"],
                   "inputPath": work["inputPath"]}
            try:
                snapshot = snapshot_file(input_path)
            except (OSError, ValueError, ArtifactSnapshotError) as exc:
                row.update(status="input_error", error=str(exc))
                prepared.append(row)
                continue
            row["snapshot"] = snapshot
            success = self.store.find_success(snapshot.sha256)
            if success and work["recoveryRecordId"] == success["successRecordId"] and verify_file(
                    Path(success["outputPath"]), success["outputSha256"]).status.value != "match":
                target = self._target_for(work)
                row["midiPath"] = str(target)
                row["recoveryRecordId"] = success["successRecordId"]
                target_key = os.path.normcase(str(target))
                row["status"] = "conflict" if target.exists() or target_key in seen_targets else "ready"
                seen_targets.add(target_key)
            elif success:
                checked = verify_file(Path(success["outputPath"]), success["outputSha256"])
                row.update(status="already_transcribed", successRecordId=success["successRecordId"],
                           resultState=str(checked.status))
            elif snapshot.sha256 in seen_hashes:
                row["status"] = "duplicate_in_batch"
            else:
                target = self._target_for(work)
                row["midiPath"] = str(target)
                target_key = os.path.normcase(str(target))
                row["status"] = "conflict" if target.exists() or target_key in seen_targets else "ready"
                seen_targets.add(target_key)
            if row["status"] == "conflict" and Path(row["midiPath"]).exists():
                try:
                    target_snapshot = snapshot_file(Path(row["midiPath"]))
                    row["targetSnapshot"] = target_snapshot
                    matches = self.store.find_successes_by_output_hash(target_snapshot.sha256)
                    if len(matches) == 1:
                        row["conflictOrigin"] = {"inputFileName": Path(matches[0]["inputPath"]).name,
                                                 "successRecordId": matches[0]["successRecordId"]}
                except (OSError, ValueError, ArtifactSnapshotError) as exc:
                    row["previewError"] = str(exc)
            seen_hashes.add(snapshot.sha256)
            prepared.append(row)
        submission_id = str(uuid4())
        self._sessions[submission_id] = {"items": prepared, "deviceMode": device_mode}
        return {"submissionId": submission_id, "items": [self._public_item(x) for x in prepared]}

    @staticmethod
    def _target_for(work: dict) -> Path:
        directory = (Path(work["inputPath"]).parent if work["outputDirectoryRule"] == "source_directory"
                     else Path(work["explicitOutputDirectory"]))
        return (directory / work["midiFileName"]).resolve(strict=False)

    @staticmethod
    def _public_item(item: dict) -> dict:
        return {key: value for key, value in item.items() if key not in ("snapshot", "targetSnapshot")}

    def preview_conflict_midi(self, submission_id: str, task_id: str) -> dict:
        session = self._sessions[submission_id]
        row = next((item for item in session["items"] if item["taskId"] == task_id), None)
        if row is None or row["status"] != "conflict" or "targetSnapshot" not in row:
            raise ValueError("MIDI 试听不可用")
        target = snapshot_file(Path(row["midiPath"]))
        if target != row["targetSnapshot"]:
            raise StaleRevision("同名 MIDI 已变化，请重新提交以核验")
        return {"taskId": task_id, "path": row["midiPath"]}

    def decide_conflict(self, submission_id: str, task_id: str, choice: str,
                        apply_to_remaining: bool = False) -> dict:
        session = self._sessions[submission_id]
        row = next((item for item in session["items"] if item["taskId"] == task_id), None)
        if row is None or row["status"] != "conflict":
            raise ValueError("task has no pending conflict")
        targets = ([row] + [item for item in session["items"] if item is not row and item["status"] == "conflict"]
                   if apply_to_remaining else [row])
        before = [(item, item["status"], item.get("midiPath"), item.get("overwrite")) for item in targets]
        try:
            changed = [self._decide_one(session, item, choice) for item in targets]
        except Exception:
            for item, status, midi_path, overwrite in before:
                item["status"] = status
                item["midiPath"] = midi_path
                if overwrite is None:
                    item.pop("overwrite", None)
                else:
                    item["overwrite"] = overwrite
            raise
        return {"items": changed} if apply_to_remaining else changed[0]

    def _decide_one(self, session: dict, row: dict, choice: str) -> dict:
        if choice == "skip":
            row["status"] = "skipped"
        elif choice == "rename":
            original = Path(row["midiPath"])
            reserved = {os.path.normcase(item.get("midiPath", "")) for item in session["items"] if item is not row
                        and item["status"] in ("ready", "conflict")}
            for number in range(1, 10000):
                candidate = original.with_name(f"{original.stem} ({number}){original.suffix}")
                if not candidate.exists() and os.path.normcase(str(candidate)) not in reserved:
                    row["midiPath"] = str(candidate)
                    row["status"] = "ready"
                    break
            else:
                raise ValueError("no free numbered MIDI name")
        elif choice == "overwrite":
            if "targetSnapshot" not in row:
                raise StaleRevision("同名 MIDI 无法核验，请重新预检")
            if any(item is not row and item["status"] == "ready" and
                   os.path.normcase(item.get("midiPath", "")) == os.path.normcase(row["midiPath"])
                   for item in session["items"]):
                raise ValueError("本批次有多个任务使用同一个 MIDI 路径，请另存编号")
            self._verify_target(row["midiPath"], row["targetSnapshot"])
            row["overwrite"] = True
            row["status"] = "ready"
        else:
            raise ValueError("invalid conflict choice")
        return self._public_item(row)

    @staticmethod
    def _verify_target(path: str, expected: FileSnapshot | None) -> None:
        if expected is None:
            if Path(path).exists():
                raise StaleRevision("目标 MIDI 已出现，请重新预检")
            return
        try:
            current = snapshot_file(Path(path))
        except (OSError, ArtifactSnapshotError) as exc:
            raise StaleRevision("同名 MIDI 无法核验，请重新预检") from exc
        if current != expected:
            raise StaleRevision("同名 MIDI 已变化，请重新预检")

    def commit_submission(self, submission_id: str) -> dict:
        session = self._sessions[submission_id]
        if any(item["status"] == "conflict" for item in session["items"]):
            raise ValueError("unresolved output conflict")
        ready = [item for item in session["items"] if item["status"] == "ready"]
        if not ready:
            raise ValueError("nothing to start")
        for item in ready:
            work = self.store.get_work_item(item["taskId"])
            if work is None or work["revision"] != item["revision"] or not work["checked"]:
                raise StaleRevision(item["taskId"])
            current = snapshot_file(Path(work["inputPath"]))
            if current != item["snapshot"]:
                raise StaleRevision("input changed after preparation")
            if item.get("recoveryRecordId"):
                success = self.store.get_success(item["recoveryRecordId"])
                if success is None or verify_file(Path(success["outputPath"]), success["outputSha256"]).status.value == "match":
                    raise StaleRevision("registered result is available again")
            self._verify_target(item["midiPath"], item.get("targetSnapshot") if item.get("overwrite") else None)
        ids = self.store.enqueue_prepared(submission_id, ready, session["deviceMode"])
        del self._sessions[submission_id]
        self._pending.extend(ids)
        self._start_next()
        return {"queued": ids, "skipped": [item["taskId"] for item in session["items"]
                                       if item["status"] == "skipped"]}

    def abandon_submission(self, submission_id: str) -> bool:
        return self._sessions.pop(submission_id, None) is not None

    def reorder_queued(self, ids: list[str], revisions: dict[str, int]) -> None:
        if ids != self._pending and (len(ids) != len(self._pending) or set(ids) != set(self._pending)):
            raise ValueError("only waiting attempts can move")
        self.store.reorder_queued(ids, revisions)
        self._pending = ids[:]

    def _start_next(self) -> None:
        if self._current is not None or self._stopping:
            return
        while self._pending:
            attempt_id = self._pending.pop(0)
            row = self.store.get_attempt(attempt_id)
            if row is None or row["status"] != "queued":
                continue
            try:
                current = snapshot_file(Path(row["input_path"]))
                if (current.sha256, current.size_bytes, current.mtime_ns) != (
                    row["input_sha256"], row["input_size"], row["input_mtime"]):
                    raise StaleRevision("input changed before worker start")
                target_snapshot = (FileSnapshot(**json.loads(row["target_snapshot"]))
                                   if row["target_snapshot"] else None)
                self._verify_target(row["midi_path"], target_snapshot)
                self.store.mark_running(attempt_id)
                task = Task(attempt_id, Path(row["input_path"]), Path(row["midi_path"]),
                            row["batch_size"], utc_now(), device_mode=row["device_mode"],
                            status=TaskStatus.RUNNING, overwrite=target_snapshot is not None,
                            target_snapshot=target_snapshot)
                self._handle = self.starter.start(task)
                self._current = attempt_id
                return
            except Exception as exc:
                self.store.finish_attempt(attempt_id, "failed", error_code="start_failed", error_message=str(exc))

    def tick(self) -> None:
        if self._current is None or self._handle is None:
            return
        attempt_id, handle = self._current, self._handle
        try:
            result = handle.poll()
        except Exception as exc:
            result = None
            self.store.finish_attempt(attempt_id, "failed", error_code="worker_exited", error_message=str(exc))
            self._current = self._handle = None
            self._start_next()
            return
        if result is None:
            return
        row = self.store.get_attempt(attempt_id)
        if row is None or row["status"] != "running":
            self._current = self._handle = None
            self._start_next()
            return
        if result.status == "succeeded":
            try:
                source = snapshot_file(Path(row["input_path"]))
                output = snapshot_file(Path(row["midi_path"]))
                if (source.sha256, source.size_bytes) != (row["input_sha256"], row["input_size"]):
                    raise StaleRevision("input changed during transcription")
                if result.input_snapshot and result.input_snapshot.sha256 != source.sha256:
                    raise StaleRevision("worker used different input")
                if result.output_snapshot and result.output_snapshot.sha256 != output.sha256:
                    raise StaleRevision("output changed after worker completion")
                self.store.finish_attempt(attempt_id, "succeeded", output_sha256=output.sha256,
                                          output_size=output.size_bytes, output_mtime=output.mtime_ns)
            except Exception as exc:
                self.store.finish_attempt(attempt_id, "failed", error_code="result_unverified", error_message=str(exc))
        else:
            self.store.finish_attempt(attempt_id, "failed", error_code=result.error_code or "transcription_failed",
                                      error_message=result.error_message or "转录失败")
        self._current = self._handle = None
        self._start_next()

    def cancel_attempt(self, attempt_id: str) -> bool:
        row = self.store.get_attempt(attempt_id)
        if row is None or row["status"] not in ("queued", "running"):
            return False
        if attempt_id == self._current:
            self.store.finish_attempt(attempt_id, "cancelled", error_code="cancelled_by_user")
            self._handle.terminate()
            self._current = self._handle = None
            self._start_next()
        else:
            self._pending.remove(attempt_id)
            self.store.finish_attempt(attempt_id, "cancelled", error_code="cancelled_by_user")
        return True

    def stop_all(self) -> None:
        self._stopping = True
        if self._current is not None:
            self.cancel_attempt(self._current)
        for attempt_id in list(self._pending):
            self.cancel_attempt(attempt_id)
