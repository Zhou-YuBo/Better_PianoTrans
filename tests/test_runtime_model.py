from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.model import (
    HistoryStatus,
    Task,
    TaskStatus,
    TransitionError,
    apply_engine_status,
    new_attempt_id,
    new_history_id,
    new_task_id,
    recover_interrupted_task,
)


class RuntimeModelTest(unittest.TestCase):
    def test_ids_are_distinct_nonempty_strings(self) -> None:
        factories = (new_task_id, new_attempt_id, new_history_id)
        for factory in factories:
            first = factory()
            second = factory()
            self.assertIsInstance(first, str)
            self.assertNotEqual(first, second)
            self.assertTrue(first)

    def test_valid_lifecycle_and_history_mapping(self) -> None:
        task = Task.create(
            input_path=Path("D:/音乐/肖邦 - 夜曲.mp3"),
            midi_path=Path("D:/输出/肖邦 - 夜曲.mid"),
            batch_size=16,
        )
        self.assertEqual(task.status, TaskStatus.DRAFT)
        task.mark_ready()
        task.enqueue()
        task.start()
        task.succeed()
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(task.history_status, HistoryStatus.SUCCEEDED)

    def test_illegal_transition_and_immutable_fields_fail(self) -> None:
        task = Task.create(Path("input.mp3"), Path("out.mid"), 4)
        with self.assertRaises(TransitionError):
            task.start()
        task.mark_ready()
        task.enqueue()
        with self.assertRaises(TransitionError):
            task.update_configuration(Path("other.mid"), 16)

    def test_duplicate_and_unknown_engine_events_are_ignored(self) -> None:
        task = Task.create(Path("a.mp3"), Path("a.mid"), 4)
        task.mark_ready()
        task.enqueue()
        self.assertTrue(apply_engine_status(task, "running"))
        self.assertTrue(apply_engine_status(task, "succeeded"))
        self.assertFalse(apply_engine_status(task, "succeeded"))
        self.assertFalse(apply_engine_status(task, "unknown"))
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)

    def test_recovery_marks_active_task_failed(self) -> None:
        task = Task.create(Path("a.mp3"), Path("a.mid"), 4)
        task.mark_ready()
        task.enqueue()
        task.start()
        recovered = recover_interrupted_task(task, datetime(2026, 7, 31, tzinfo=timezone.utc))
        self.assertEqual(recovered.status, TaskStatus.FAILED)
        self.assertEqual(recovered.error_code, "app_interrupted")
        self.assertEqual(recovered.history_status, HistoryStatus.FAILED)

    def test_long_path_and_positive_batch_validation(self) -> None:
        path = Path("D:/" + "深" * 130 + "/肖邦练习曲.mp3")
        task = Task.create(path, path.with_suffix(".mid"), 1)
        self.assertEqual(task.input_path, path)
        with self.assertRaises(ValueError):
            Task.create(path, path.with_suffix(".mid"), 0)


if __name__ == "__main__":
    unittest.main()
