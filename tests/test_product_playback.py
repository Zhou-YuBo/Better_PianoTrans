"""Single-entry playback session behavior independent of list selection."""

import tempfile
import unittest
from pathlib import Path

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.product_contract import EntryKind, EntryRef
from pianotrans_backend.product_files import ProductFileService
from pianotrans_backend.product_playback import ProductPlaybackService
from pianotrans_backend.product_store import ProductRepository


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.callback = None

    def set_event_callback(self, callback):
        self.callback = callback

    def prepare(self, path):
        self.calls.append(("prepare", path))
        return True

    def start(self):
        self.calls.append(("start", None))

    def pause(self):
        self.calls.append(("pause", None))

    def stop(self):
        self.calls.append(("stop", None))

    def seek(self, seconds):
        self.calls.append(("seek", seconds))

    def set_volume(self, value):
        self.calls.append(("volume", value))

    def close(self, **_):
        self.calls.append(("close", None))


class ProductPlaybackTests(unittest.TestCase):
    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.store = ProductRepository(self.root / "product.db")
        self.audio = self.root / "a.wav"
        self.midi = self.root / "a.mid"
        self.audio.write_bytes(b"RIFF")
        self.midi.write_bytes(b"MThd")
        a, m = snapshot_file(self.audio), snapshot_file(self.midi)
        self.store.create_attempt("a1", "t1", "s1", str(self.audio), str(self.midi), a.sha256, a.size_bytes, a.mtime_ns)
        self.store.finish_attempt("a1", "succeeded", output_sha256=m.sha256,
                                  output_size=m.size_bytes, output_mtime=m.mtime_ns)
        self.favorite = self.store.add_favorite("a1")["favoriteId"]
        self.backends = []

        def factory():
            pair = (FakeBackend(), FakeBackend())
            self.backends.append(pair)
            return pair

        self.player = ProductPlaybackService(ProductFileService(self.store), factory)

    def tearDown(self):
        self.player.close()
        self.store.close()
        self.temp.cleanup()

    def test_favorite_midi_plays_after_history_deleted_and_stop_keeps_session(self):
        self.store.delete_history(["a1"])
        result = self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        self.assertEqual("playing", result["state"])
        session = result["sessionId"]
        self.player.stop(session)
        self.assertEqual("stopped", self.player.snapshot()["state"])
        self.player.seek(session, 1.0)
        self.assertEqual("playing", self.player.snapshot()["state"])
        self.assertEqual(session, self.player.snapshot()["sessionId"])

    def test_missing_audio_does_not_auto_downgrade_paired_request(self):
        self.audio.unlink()
        result = self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "paired")
        self.assertEqual("needs_file_resolution", result["state"])
        self.assertIn("midi_only", result["allowedActions"])
        self.assertIsNone(self.player.snapshot()["sessionId"])

    def test_single_track_end_loops_same_session(self):
        result = self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        midi = self.backends[-1][1]
        midi.callback({"type": "ended"})
        self.assertEqual(2, len([x for x in midi.calls if x[0] == "start"]))
        self.assertEqual(result["sessionId"], self.player.snapshot()["sessionId"])

    def test_midi_ready_event_supplies_progress_duration(self):
        self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        midi = self.backends[-1][1]
        midi.callback({"type": "ready", "duration": 42.5})
        self.assertEqual(42.5, self.player.snapshot()["durations"]["midi"])

    def test_seek_resumes_midi_only_when_playing(self):
        result = self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        midi = self.backends[-1][1]
        self.player.seek(result["sessionId"], 12.0)
        self.assertEqual([("seek", 12.0), ("start", None)], midi.calls[-2:])
        self.player.pause(result["sessionId"])
        self.player.seek(result["sessionId"], 15.0)
        self.assertEqual(("seek", 15.0), midi.calls[-1])
        self.assertEqual("paused", self.player.snapshot()["state"])

    def test_paired_seek_keeps_audio_running_and_restarts_midi(self):
        result = self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "paired")
        audio, midi = self.backends[-1]
        self.player.seek(result["sessionId"], 8.0)
        self.assertEqual(("seek", 8.0), audio.calls[-1])
        self.assertEqual([("seek", 8.0), ("start", None)], midi.calls[-2:])

    def test_adjusting_volume_while_muted_unmutes_at_new_level(self):
        self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        midi = self.backends[-1][1]
        self.player.set_muted("midi", True)
        self.assertEqual(("volume", 0.0), midi.calls[-1])
        self.player.set_volume("midi", 0.3)
        self.assertEqual(("volume", 0.3), midi.calls[-1])
        self.assertFalse(self.player.snapshot()["muted"]["midi"])
        self.assertEqual(0.3, self.player.snapshot()["volumes"]["midi"])

    def test_mute_button_restores_saved_volume(self):
        self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        midi = self.backends[-1][1]
        self.player.set_volume("midi", 0.4)
        self.player.set_muted("midi", True)
        self.player.set_muted("midi", False)
        self.assertEqual(("volume", 0.4), midi.calls[-1])
        self.assertEqual(0.4, self.player.snapshot()["volumes"]["midi"])

    def test_old_backend_event_cannot_start_new_session(self):
        events = []
        self.player.set_event_callback(events.append)
        self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        old = self.backends[-1][1]
        self.player.request(EntryRef(EntryKind.FAVORITE, self.favorite), "audio")
        self.assertTrue(any(event["type"] == "notes_cleared" for event in events))
        old.callback({"type": "ended"})
        self.assertEqual("audio", self.player.snapshot()["mode"])
        self.assertEqual(1, len([x for x in self.backends[-1][0].calls if x[0] == "start"]))

    def test_conflict_midi_uses_the_persistent_player(self):
        result = self.player.request_midi_path(str(self.midi), "task-1")
        self.assertEqual({"kind": "conflict", "id": "task-1"}, result["source"])
        self.assertEqual("playing", result["state"])
        self.assertIn(("prepare", str(self.midi)), self.backends[-1][1].calls)
        self.player.pause(result["sessionId"])
        self.assertEqual("paused", self.player.snapshot()["state"])


if __name__ == "__main__":
    unittest.main()
