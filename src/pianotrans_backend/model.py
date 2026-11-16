"""Domain objects for desktop transcription tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from .artifacts import FileSnapshot
from .devices import validate_device_mode


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REMOVED = "removed"


class HistoryStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransitionError(ValueError):
    """Raised when a task receives an impossible state transition."""


def new_task_id() -> str:
    return uuid4().hex


def new_attempt_id() -> str:
    return uuid4().hex


def new_history_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Task:
    task_id: str
    input_path: Path
    midi_path: Path
    batch_size: int
    created_at: datetime
    overwrite: bool = False
    target_snapshot: FileSnapshot | None = None
    device_mode: str = "auto"
    status: TaskStatus = TaskStatus.DRAFT
    attempt_id: str | None = None
    history_id: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def create(
        cls,
        input_path: Path,
        midi_path: Path,
        batch_size: int,
        now: datetime | None = None,
        overwrite: bool = False,
        device_mode: str = "auto",
    ) -> "Task":
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        validate_device_mode(device_mode)
        return cls(new_task_id(), input_path, midi_path, batch_size, now or utc_now(), overwrite, device_mode=device_mode)

    @property
    def history_status(self) -> HistoryStatus | None:
        if self.status == TaskStatus.SUCCEEDED:
            return HistoryStatus.SUCCEEDED
        if self.status == TaskStatus.FAILED:
            return HistoryStatus.FAILED
        if self.status == TaskStatus.CANCELLED:
            return HistoryStatus.CANCELLED
        return None

    def mark_ready(self) -> None:
        self._transition(TaskStatus.READY, {TaskStatus.DRAFT, TaskStatus.FAILED})
        self._clear_terminal_details()

    def update_configuration(self, midi_path: Path, batch_size: int) -> None:
        if self.status not in {TaskStatus.DRAFT, TaskStatus.READY}:
            raise TransitionError("only unstarted tasks can be configured")
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.midi_path = midi_path
        self.batch_size = batch_size

    def enqueue(self, now: datetime | None = None) -> None:
        self._transition(TaskStatus.QUEUED, {TaskStatus.READY})
        self.attempt_id = new_attempt_id()
        self.history_id = new_history_id()
        self.queued_at = now or utc_now()
        self.started_at = self.queued_at

    def start(self) -> None:
        self._transition(TaskStatus.RUNNING, {TaskStatus.QUEUED})

    def succeed(self, now: datetime | None = None) -> None:
        self._transition(TaskStatus.SUCCEEDED, {TaskStatus.RUNNING})
        self.finished_at = now or utc_now()

    def fail(self, code: str, message: str, now: datetime | None = None) -> None:
        self._transition(TaskStatus.FAILED, {TaskStatus.QUEUED, TaskStatus.RUNNING})
        self.error_code = code
        self.error_message = message
        self.finished_at = now or utc_now()

    def cancel(self, now: datetime | None = None) -> None:
        self._transition(TaskStatus.CANCELLED, {TaskStatus.QUEUED, TaskStatus.RUNNING})
        self.error_code = "cancelled_by_user"
        self.error_message = "已取消"
        self.finished_at = now or utc_now()

    def remove(self) -> None:
        self._transition(TaskStatus.REMOVED, {TaskStatus.DRAFT, TaskStatus.READY, TaskStatus.FAILED})

    def _transition(self, target: TaskStatus, allowed: set[TaskStatus]) -> None:
        if self.status not in allowed:
            raise TransitionError(f"cannot transition from {self.status} to {target}")
        self.status = target

    def _clear_terminal_details(self) -> None:
        self.finished_at = None
        self.error_code = None
        self.error_message = None


def apply_engine_status(task: Task, status: str, error: dict | None = None) -> bool:
    """Apply a valid engine status once; ignore duplicate and unknown events."""
    try:
        if status == "running" and task.status == TaskStatus.QUEUED:
            task.start()
        elif status == "succeeded" and task.status == TaskStatus.RUNNING:
            task.succeed()
        elif status == "failed" and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            details = error or {}
            task.fail(details.get("code", "transcription_failed"), details.get("message", "转录失败"))
        elif status == "cancelled" and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            task.cancel()
        else:
            return False
    except TransitionError:
        return False
    return True


def recover_interrupted_task(task: Task, now: datetime | None = None) -> Task:
    """Recover an active task after an unclean application shutdown."""
    if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        task.fail("app_interrupted", "应用意外关闭，转录未完成", now)
    return task
