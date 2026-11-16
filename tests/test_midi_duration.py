"""The exported MIDI timeline follows the decoded source audio length."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import mido
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "third_party" / "byte_dance_core" / "pytorch"),
                str(ROOT / "third_party" / "byte_dance_core" / "utils")]
from inference import PianoTranscription  # noqa: E402
from utilities import write_events_to_midi  # noqa: E402


class MidiDurationTest(unittest.TestCase):
    def test_silent_tail_keeps_audio_duration(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            path = Path(directory) / "silent-tail.mid"
            write_events_to_midi(0, [{"onset_time": 1.0, "offset_time": 2.0,
                                      "midi_note": 60, "velocity": 80}], None,
                                 str(path), end_time=10.0)
            midi = mido.MidiFile(str(path))
            self.assertAlmostEqual(midi.length, 10.0, delta=1 / 768)
            note_off = [message for message in midi.tracks[1]
                        if message.type == "note_on" and message.velocity == 0]
            self.assertEqual(len(note_off), 1)

    def test_padded_tail_events_cannot_extend_midi(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            path = Path(directory) / "padded-tail.mid"
            notes = [{"onset_time": 9.5, "offset_time": 12.0,
                      "midi_note": 60, "velocity": 80},
                     {"onset_time": 11.0, "offset_time": 12.0,
                      "midi_note": 62, "velocity": 80}]
            pedals = [{"onset_time": 9.0, "offset_time": 12.0}]
            write_events_to_midi(0, notes, pedals, str(path), end_time=10.0)
            midi = mido.MidiFile(str(path))
            self.assertAlmostEqual(midi.length, 10.0, delta=1 / 768)
            self.assertEqual([message.note for message in midi.tracks[1]
                              if message.type == "note_on" and message.velocity > 0], [60])
            self.assertEqual([message.value for message in midi.tracks[1]
                              if message.type == "control_change"], [127, 0])

    def test_prediction_frames_are_cropped_in_frame_units(self) -> None:
        transcription = PianoTranscription.__new__(PianoTranscription)
        transcription.segment_samples = 160000
        transcription.batch_size = 1
        transcription.frames_per_second = 100
        transcription.post_processor_type = "regression"
        transcription.classes_num = 88
        transcription.onset_threshold = 0.3
        transcription.offset_threshod = 0.3
        transcription.frame_threshold = 0.1
        transcription.pedal_offset_threshold = 0.2
        transcription.model = object()
        frames = np.zeros((3, 1001, 1), dtype=np.float32)
        processor = Mock()
        processor.output_dict_to_midi_events.return_value = ([], [])
        with patch("inference.forward", return_value={"frame_output": frames}), \
             patch("inference.RegressionPostProcessor", return_value=processor), \
             patch("inference.write_events_to_midi") as writer:
            result = transcription.transcribe(np.zeros(192000, dtype=np.float32), "out.mid")
        self.assertEqual(result["output_dict"]["frame_output"].shape[0], 1201)
        self.assertEqual(writer.call_args.kwargs["end_time"], 12.0)


if __name__ == "__main__":
    unittest.main()
