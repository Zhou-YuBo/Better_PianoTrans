from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.history import HistoryAttempt, HistoryRepository
from pianotrans_backend.playback import PlaybackCoordinator, PlaybackError, PlaybackState


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def prepare(self, path: str) -> bool:
        self.calls.append(("prepare", path))
        return True

    def start(self) -> None:
        self.calls.append(("start", None))

    def stop(self) -> None:
        self.calls.append(("stop", None))

    def seek(self, seconds: float) -> None:
        self.calls.append(("seek", seconds))


class PlaybackCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp")
        root = Path(self.tempdir.name)
        self.audio_path = root / "song.mp3"
        self.midi_path = root / "song.mid"
        self.audio_path.write_bytes(b"audio")
        self.midi_path.write_bytes(b"midi")
        self.repository = HistoryRepository(root / "history.db")
        audio = snapshot_file(self.audio_path)
        midi = snapshot_file(self.midi_path)
        self.record = HistoryAttempt(
            history_id="history-1", task_id="task-1", attempt_id="attempt-1",
            input_path=str(self.audio_path), midi_path=str(self.midi_path), status="queued",
            batch_size=4, started_at=datetime.now(timezone.utc),
            input_sha256=audio.sha256, input_size_bytes=audio.size_bytes, input_mtime_ns=audio.mtime_ns,
            output_sha256=midi.sha256, output_size_bytes=midi.size_bytes, output_mtime_ns=midi.mtime_ns,
        )
        self.repository.create_attempt(self.record)
        self.repository.finish_attempt(self.record.history_id, "succeeded", self.record.started_at)
        self.audio = FakeBackend()
        self.midi = FakeBackend()
        self.coordinator = PlaybackCoordinator(self.repository, self.audio, self.midi)

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_preparation_barrier_and_start(self) -> None:
        self.coordinator.prepare_history(self.record.history_id)
        self.assertEqual(self.coordinator.state, PlaybackState.READY)
        self.coordinator.start()
        self.assertEqual(self.coordinator.state, PlaybackState.PLAYING)
        self.assertEqual([call[0] for call in self.audio.calls], ["stop", "prepare", "start"])
        self.assertEqual([call[0] for call in self.midi.calls], ["stop", "prepare", "start"])

    def test_mismatch_requires_one_time_confirmation(self) -> None:
        self.audio_path.write_bytes(b"replacement")
        with self.assertRaises(PlaybackError) as context:
            self.coordinator.prepare_history(self.record.history_id)
        self.assertEqual(context.exception.verification[0].status.value, "mismatch")
        self.coordinator.prepare_history(self.record.history_id, allow_unverified=True)
        self.assertEqual(self.coordinator.state, PlaybackState.READY)


if __name__ == "__main__":
    unittest.main()
