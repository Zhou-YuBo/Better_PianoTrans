"""Isolated process that performs one transcription and commits its MIDI atomically."""

from __future__ import annotations

import os
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
from typing import Any

from .artifacts import ArtifactSnapshotError, FileSnapshot, snapshot_file
from .model import Task
from .output import AtomicMidiOutput, OutputCommitError
from .scheduler import WorkerHandle, WorkerResult


@dataclass
class ProcessWorkerHandle(WorkerHandle):
    process: Any
    result_queue: Any
    _reported_exit: bool = False

    def poll(self) -> WorkerResult | None:
        try:
            payload = self.result_queue.get_nowait()
        except Empty:
            if self.process.is_alive() or self._reported_exit:
                return None
            self._reported_exit = True
            return WorkerResult.failed("worker_exited", "转录引擎意外退出")
        return WorkerResult(**payload)

    def terminate(self) -> None:
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)


@dataclass(frozen=True)
class ProcessWorkerStarter:
    checkpoint: Path
    project_root: Path

    def start(self, task: Task) -> ProcessWorkerHandle:
        context = get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=run_transcription_worker,
            args=(
                result_queue,
                task.task_id,
                task.input_path,
                task.midi_path,
                task.batch_size,
                task.overwrite,
                task.device_mode,
                self.checkpoint,
                self.project_root,
                task.target_snapshot,
            ),
            daemon=True,
        )
        process.start()
        return ProcessWorkerHandle(process, result_queue)


def run_transcription_worker(
    result_queue: Any,
    task_id: str,
    input_path: Path,
    midi_path: Path,
    batch_size: int,
    overwrite: bool,
    device_mode: str,
    checkpoint: Path,
    project_root: Path,
    target_snapshot: FileSnapshot | None = None,
) -> None:
    """Process entry. It must not print protocol data to standard output."""
    os.environ.setdefault("MPLCONFIGDIR", str(project_root / ".codex-cache" / "matplotlib"))
    output = AtomicMidiOutput(midi_path, task_id)
    input_snapshot = None
    runtime_metadata = {
        "app_version": os.environ.get("PIANOTRANS_APP_VERSION", "0.1.0-dev"),
        "model_revision": checkpoint.stem,
        "post_processor": "regression",
    }
    try:
        input_snapshot = snapshot_file(input_path)
        temporary_path = output.prepare()
        from .cli import transcribe_one

        response = transcribe_one(
            input_path,
            temporary_path.parent,
            checkpoint,
            overwrite=True,
            batch_size=batch_size,
            midi_path=temporary_path,
            device_mode=device_mode,
        )
        if response["status"] != "succeeded":
            error = response.get("error") or {}
            result_queue.put(
                {
                    "status": "failed",
                    "error_code": _error_code(error.get("code", "transcription_failed")),
                    "error_message": _error_message(error),
                    "input_snapshot": input_snapshot,
                "input_duration_ms": response.get("input_duration_ms"),
                "input_sample_rate": response.get("input_sample_rate"),
                "input_channels": response.get("input_channels"),
                "device_mode": response.get("device_mode", device_mode),
                "execution_device": response.get("execution_device"),
                **runtime_metadata,
                }
            )
            return
        current_input = snapshot_file(input_path)
        if current_input.sha256 != input_snapshot.sha256:
            result_queue.put(
                {
                    "status": "failed",
                    "error_code": "input_changed",
                    "error_message": "转录期间输入音频发生变化。",
                    "input_snapshot": input_snapshot,
                    "device_mode": response.get("device_mode", device_mode),
                    "execution_device": response.get("execution_device"),
                    **runtime_metadata,
                }
            )
            return
        output.commit(overwrite=overwrite, expected_target=target_snapshot)
        output_snapshot = snapshot_file(midi_path)
        result_queue.put(
            {
                "status": "succeeded",
                "midi_path": midi_path,
                "input_snapshot": input_snapshot,
                "output_snapshot": output_snapshot,
                "input_duration_ms": response.get("input_duration_ms"),
                "input_sample_rate": response.get("input_sample_rate"),
                    "input_channels": response.get("input_channels"),
                    "device_mode": response.get("device_mode", device_mode),
                    "execution_device": response.get("execution_device"),
                    **runtime_metadata,
            }
        )
    except FileNotFoundError:
        result_queue.put(
            {
                "status": "failed",
                "error_code": "input_missing",
                "error_message": "输入音频不存在。",
                "input_snapshot": input_snapshot,
                "device_mode": device_mode,
                **runtime_metadata,
            }
        )
    except ArtifactSnapshotError as exc:
        result_queue.put(
            {
                "status": "failed",
                "error_code": "input_changed",
                "error_message": str(exc),
                "input_snapshot": input_snapshot,
                "device_mode": device_mode,
                **runtime_metadata,
            }
        )
    except OutputCommitError as exc:
        result_queue.put(
            {
                "status": "failed",
                "error_code": "midi_commit_failed",
                "error_message": str(exc),
                "input_snapshot": input_snapshot,
                "device_mode": device_mode,
                **runtime_metadata,
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                "status": "failed",
                "error_code": "transcription_failed",
                "error_message": str(exc),
                "input_snapshot": input_snapshot,
                "device_mode": device_mode,
                **runtime_metadata,
            }
        )
    finally:
        output.cleanup()


def _error_code(code: object) -> str:
    mapping = {
        "input_not_found": "input_missing",
        "checkpoint_not_found": "checkpoint_unavailable",
        "output_exists": "output_conflict",
        "transcription_failed": "transcription_failed",
        "cuda_unavailable": "cuda_unavailable",
    }
    return mapping.get(str(code), "transcription_failed")


def _error_message(error: dict) -> str:
    message = error.get("message")
    return str(message) if message else "转录失败"
