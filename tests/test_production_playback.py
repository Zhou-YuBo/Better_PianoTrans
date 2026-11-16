from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mido

from pianotrans_backend.midi_player import MidiTimeline
from pianotrans_backend.playback import PlaybackCoordinator, PlaybackMode, PlaybackState


class FakeBackend:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[tuple[str, object]] = []

    def prepare(self, path: str) -> bool:
        self.calls.append(("prepare", path))
        return self.ready

    def start(self) -> None: self.calls.append(("start", None))
    def pause(self) -> None: self.calls.append(("pause", None))
    def stop(self) -> None: self.calls.append(("stop", None))
    def seek(self, seconds: float) -> None: self.calls.append(("seek", seconds))
    def set_volume(self, value: float) -> None: self.calls.append(("volume", value))


class ProductionPlaybackTest(unittest.TestCase):
    def test_timeline_handles_tempo_and_seek(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(".codex-tmp")) as directory:
            path = Path(directory) / "test.mid"
            midi = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
            track.append(mido.Message("note_on", note=60, velocity=80, time=480))
            track.append(mido.Message("note_off", note=60, velocity=0, time=480))
            midi.tracks.append(track)
            midi.save(path)
            timeline = MidiTimeline.read(path)
            self.assertAlmostEqual(timeline.duration, 1.0, places=2)
            self.assertEqual(timeline.seconds_to_tick(0.5), 480)
            seek_path = timeline.create_seek_file(480, Path(directory))
            self.assertTrue(seek_path.is_file())

    def test_audio_only_and_midi_only_lifecycle(self) -> None:
        audio = FakeBackend(); midi = FakeBackend()
        coordinator = PlaybackCoordinator(None, audio, midi)  # type: ignore[arg-type]
        coordinator.prepare_audio("song.wav")
        self.assertEqual(coordinator.mode, PlaybackMode.AUDIO_ONLY)
        self.assertEqual(coordinator.state, PlaybackState.READY)
        coordinator.start(); coordinator.pause(); coordinator.seek(2.0); coordinator.stop()
        self.assertIn(("start", None), audio.calls)
        coordinator.prepare_midi("song.mid")
        self.assertEqual(coordinator.mode, PlaybackMode.MIDI_ONLY)
        coordinator.start()
        self.assertIn(("start", None), midi.calls)


if __name__ == "__main__":
    unittest.main()
