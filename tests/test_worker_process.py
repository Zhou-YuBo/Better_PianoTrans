"""Windows + NVIDIA acceptance tests for the isolated transcription worker."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from time import monotonic, sleep


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".codex-cache" / "matplotlib"))

from pianotrans_backend.cli import DEFAULT_CHECKPOINT
from pianotrans_backend.history import HistoryRepository
from pianotrans_backend.model import Task, TaskStatus
from pianotrans_backend.scheduler import Scheduler
from pianotrans_backend.worker import ProcessWorkerStarter


@unittest.skipUnless(DEFAULT_CHECKPOINT.exists(), "checkpoint is unavailable")
class WorkerProcessAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp")
        self.root = Path(self.tempdir.name)
        self.repository = HistoryRepository(self.root / "history.db")
        self.scheduler = Scheduler(
            self.repository,
            ProcessWorkerStarter(DEFAULT_CHECKPOINT, ROOT),
        )

    def tearDown(self) -> None:
        self.scheduler.shutdown(stop_all=True)
        self.repository.close()
        self.tempdir.cleanup()

    def ready_task(self, fixture: str, name: str, device_mode: str = "auto") -> Task:
        task = Task.create(ROOT / "tests" / "fixtures" / fixture, self.root / f"{name}.mid", 16, device_mode=device_mode)
        task.mark_ready()
        return task

    def test_real_worker_writes_final_midi(self) -> None:
        task = self.ready_task("liszt_10s_16k_mono.wav", "real-output")
        self.scheduler.submit(task)
        deadline = monotonic() + 120
        while task.status == TaskStatus.RUNNING and monotonic() < deadline:
            self.scheduler.tick()
            sleep(0.05)
        self.scheduler.tick()
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertTrue(task.midi_path.is_file())
        self.assertFalse(any(self.root.glob("*.pianotrans.tmp")))
        history = self.repository.get_attempt(task.history_id or "")
        self.assertIsNotNone(history)
        self.assertTrue(history.input_sha256)
        self.assertTrue(history.output_sha256)
        self.assertEqual(history.input_sample_rate, 16000)
        self.assertEqual(history.input_channels, 1)

    def test_real_worker_cpu_writes_final_midi(self) -> None:
        task = self.ready_task("liszt_10s_16k_mono.wav", "real-cpu-output", device_mode="cpu")
        self.scheduler.submit(task)
        deadline = monotonic() + 120
        while task.status == TaskStatus.RUNNING and monotonic() < deadline:
            self.scheduler.tick()
            sleep(0.05)
        self.scheduler.tick()
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertTrue(task.midi_path.is_file())
        history = self.repository.get_attempt(task.history_id or "")
        self.assertEqual(history.device_mode, "cpu")
        self.assertEqual(history.execution_device, "cpu")

    def test_real_worker_cancellation_does_not_wait_for_completion(self) -> None:
        task = self.ready_task("liszt_40s_16k_mono.wav", "cancelled-output")
        self.scheduler.submit(task)
        sleep(1)
        started = monotonic()
        self.assertTrue(self.scheduler.cancel(task.task_id))
        self.assertLess(monotonic() - started, 2)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertFalse(task.midi_path.exists())
        self.assertFalse(any(self.root.glob("*.pianotrans.tmp")))

    def test_real_queue_reorders_and_isolates_failure(self) -> None:
        first = self.ready_task("liszt_10s_16k_mono.wav", "first")
        missing = self.ready_task("missing.wav", "missing")
        third = self.ready_task("liszt_10s_16k_mono.wav", "third")
        self.scheduler.submit(first)
        self.scheduler.submit(missing)
        self.scheduler.submit(third)
        self.scheduler.reorder([third.task_id, missing.task_id])

        deadline = monotonic() + 180
        while any(task.status in {TaskStatus.RUNNING, TaskStatus.QUEUED} for task in (first, missing, third)) and monotonic() < deadline:
            self.scheduler.tick()
            sleep(0.05)
        self.scheduler.tick()

        self.assertEqual(first.status, TaskStatus.SUCCEEDED)
        self.assertEqual(third.status, TaskStatus.SUCCEEDED)
        self.assertEqual(missing.status, TaskStatus.FAILED)
        self.assertTrue(third.midi_path.exists())


if __name__ == "__main__":
    unittest.main()
