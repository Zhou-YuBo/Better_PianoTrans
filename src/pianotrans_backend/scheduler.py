"""Single-GPU FIFO scheduler with immediately terminable worker processes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from .artifacts import FileSnapshot
from .history import HistoryAttempt, HistoryRepository
from .model import Task, TaskStatus, utc_now


@dataclass(frozen=True)
class WorkerResult:
    status: str
    midi_path: Path | None = None
    error_code: str | None = None
    error_message: str | None = None
    input_snapshot: FileSnapshot | None = None
    output_snapshot: FileSnapshot | None = None
    input_duration_ms: int | None = None
    input_sample_rate: int | None = None
    input_channels: int | None = None
    app_version: str | None = None
    model_revision: str | None = None
    post_processor: str | None = None
    device_mode: str | None = None
    execution_device: str | None = None

    @classmethod
    def succeeded(cls, midi_path: Path) -> "WorkerResult":
        return cls("succeeded", midi_path=midi_path)

    @classmethod
    def failed(cls, code: str, message: str) -> "WorkerResult":
        return cls("failed", error_code=code, error_message=message)


class WorkerHandle(Protocol):
    def poll(self) -> WorkerResult | None: ...

    def terminate(self) -> None: ...


class WorkerStarter(Protocol):
    def start(self, task: Task) -> WorkerHandle: ...


SchedulerObserver = Callable[[str, Task], None]
Clock = Callable[[], datetime]
TempCleanup = Callable[[Task], None]


class Scheduler:
    """Runs one task at a time and persists every queued attempt."""

    def __init__(
        self,
        repository: HistoryRepository,
        starter: WorkerStarter,
        observer: SchedulerObserver | None = None,
        clock: Clock = utc_now,
        cleanup_temp: TempCleanup | None = None,
    ) -> None:
        self._repository = repository
        self._starter = starter
        self._observer = observer
        self._clock = clock
        self._cleanup_temp = cleanup_temp or _cleanup_task_temp
        self._tasks: dict[str, Task] = {}
        self._pending: list[str] = []
        self._current_task_id: str | None = None
        self._worker: WorkerHandle | None = None
        self._stopping = False

    @property
    def current_task_id(self) -> str | None:
        return self._current_task_id

    def submit(self, task: Task) -> None:
        if task.task_id in self._tasks:
            raise ValueError("task_id already exists")
        task.enqueue(self._clock())
        self._tasks[task.task_id] = task
        self._pending.append(task.task_id)
        self._repository.create_attempt(_attempt_from_task(task))
        self._publish("queued", task)
        self._start_next()

    def reorder(self, task_ids: list[str]) -> None:
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task IDs must not repeat")
        selected = set(task_ids)
        if not selected.issubset(self._pending):
            raise ValueError("only queued tasks can be reordered")
        remainder = [task_id for task_id in self._pending if task_id not in selected]
        self._pending = task_ids + remainder

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return False
        task.cancel(self._clock())
        self._finish_history(task)
        if task_id == self._current_task_id:
            if self._worker is not None:
                self._worker.terminate()
            self._cleanup_temp(task)
            self._worker = None
            self._current_task_id = None
            self._publish("cancelled_running", task)
            self._start_next()
        else:
            self._pending.remove(task_id)
            self._cleanup_temp(task)
            self._publish("cancelled_queued", task)
        return True

    def tick(self) -> None:
        if self._worker is None or self._current_task_id is None:
            return
        result = self._worker.poll()
        if result is None:
            return
        task = self._tasks[self._current_task_id]
        if result.status == "succeeded":
            task.succeed(self._clock())
        else:
            task.fail(result.error_code or "transcription_failed", result.error_message or "转录失败", self._clock())
            self._cleanup_temp(task)
        if result.input_snapshot is not None:
            self._repository.update_input_snapshot(
                task.history_id or "",
                result.input_snapshot,
                result.input_duration_ms,
                result.input_sample_rate,
                result.input_channels,
            )
        if result.output_snapshot is not None:
            self._repository.update_output_snapshot(task.history_id or "", result.output_snapshot)
        if result.app_version or result.model_revision or result.post_processor:
            self._repository.update_runtime_metadata(
                task.history_id or "", result.app_version, result.model_revision, result.post_processor
            )
        if result.device_mode or result.execution_device:
            self._repository.update_device_metadata(
                task.history_id or "", result.device_mode, result.execution_device
            )
        self._finish_history(task)
        self._publish("succeeded" if result.status == "succeeded" else "failed", task)
        self._worker = None
        self._current_task_id = None
        self._start_next()

    def shutdown(self, stop_all: bool) -> None:
        if not stop_all:
            return
        self._stopping = True
        if self._current_task_id is not None:
            self.cancel(self._current_task_id)
        for task_id in list(self._pending):
            self.cancel(task_id)

    def recover_interrupted_attempts(self) -> int:
        return self._repository.recover_active_attempts(self._clock())

    def _start_next(self) -> None:
        if self._stopping or self._current_task_id is not None:
            return
        while self._pending:
            task_id = self._pending.pop(0)
            task = self._tasks[task_id]
            if task.status != TaskStatus.QUEUED:
                continue
            task.start()
            task.started_at = self._clock()
            self._repository.mark_running(task.history_id or "", task.started_at)
            self._current_task_id = task_id
            self._worker = self._starter.start(task)
            self._publish("started", task)
            return

    def _finish_history(self, task: Task) -> None:
        if task.history_id is None or task.history_status is None or task.finished_at is None:
            raise RuntimeError("terminal task is missing history metadata")
        self._repository.finish_attempt(
            task.history_id,
            str(task.history_status),
            task.finished_at,
            task.error_code,
            task.error_message,
        )

    def _publish(self, event: str, task: Task) -> None:
        if self._observer is not None:
            self._observer(event, task)


def _attempt_from_task(task: Task) -> HistoryAttempt:
    if task.history_id is None or task.attempt_id is None or task.started_at is None:
        raise RuntimeError("queued task is missing attempt metadata")
    return HistoryAttempt(
        history_id=task.history_id,
        task_id=task.task_id,
        attempt_id=task.attempt_id,
        input_path=str(task.input_path),
        midi_path=str(task.midi_path),
        status="queued",
        batch_size=task.batch_size,
        started_at=task.started_at,
        queued_at=task.queued_at or task.started_at,
        device_mode=task.device_mode,
    )


def _cleanup_task_temp(task: Task) -> None:
    from .output import AtomicMidiOutput

    AtomicMidiOutput(task.midi_path, task.task_id).cleanup()
