"""Windows + NVIDIA acceptance tests for the isolated transcription worker."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from queue import SimpleQueue
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".codex-cache" / "matplotlib"))

from pianotrans_backend.cli import DEFAULT_CHECKPOINT
from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.history import HistoryRepository
from pianotrans_backend.model import Task, TaskStatus
from pianotrans_backend.scheduler import Scheduler
from pianotrans_backend.worker import ProcessWorkerStarter, run_transcription_worker


class WorkerOverwriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp")
        self.root = Path(self.tempdir.name)
        self.audio = self.root / "input.wav"
        self.midi = self.root / "output.mid"
        self.audio.write_bytes(b"audio")
        self.midi.write_bytes(b"old midi")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_worker(self, change_target: bool) -> dict:
        expected = snapshot_file(self.midi)

        def transcribe(_source, _directory, _checkpoint, **options):
            options["midi_path"].write_bytes(b"new midi")
            if change_target:
                self.midi.write_bytes(b"external midi")
            return {"status": "succeeded"}

        result_queue = SimpleQueue()
        with patch("pianotrans_backend.cli.transcribe_one", side_effect=transcribe):
            run_transcription_worker(result_queue, "attempt", self.audio, self.midi, 16,
                                     True, "cpu", self.root / "checkpoint.pt", self.root, expected)
        return result_queue.get_nowait()

    def test_worker_replaces_confirmed_midi(self) -> None:
        self.assertEqual("succeeded", self.run_worker(False)["status"])
        self.assertEqual(b"new midi", self.midi.read_bytes())

    def test_worker_preserves_midi_changed_during_transcription(self) -> None:
        result = self.run_worker(True)
        self.assertEqual("failed", result["status"])
        self.assertEqual("midi_commit_failed", result["error_code"])
        self.assertEqual(b"external midi", self.midi.read_bytes())


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
