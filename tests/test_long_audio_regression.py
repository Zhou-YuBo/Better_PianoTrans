"""GPU regression test for the fixed 40-second piano input."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.cli import DEFAULT_CHECKPOINT


CHECKPOINT = DEFAULT_CHECKPOINT


@unittest.skipUnless(CHECKPOINT.exists(), "checkpoint is unavailable")
class LongAudioRegressionTest(unittest.TestCase):
    def test_fixed_outputs(self) -> None:
        environment = os.environ.copy()
        environment["MPLCONFIGDIR"] = str(ROOT / ".codex-cache" / "matplotlib")
        environment["TEMP"] = str(ROOT / ".codex-tmp")
        environment["TMP"] = str(ROOT / ".codex-tmp")
        script = """
import sys
from pathlib import Path
import numpy as np
import torch

root = Path.cwd()
sys.path.insert(0, str(root / 'src'))
from pianotrans_backend.cli import DEFAULT_CHECKPOINT
from inference import PianoTranscription
from utilities import load_audio

audio, _ = load_audio(str(root / 'tests/fixtures/liszt_40s_16k_mono.wav'), sr=16000, mono=True)
transcriptor = PianoTranscription('Note_pedal', checkpoint_path=str(DEFAULT_CHECKPOINT), device=torch.device('cuda'), post_processor_type='regression')
result = transcriptor.transcribe(audio, str(root / '.codex-tmp/transcription_40s.mid'))
np.savez(root / '.codex-tmp/raw_predictions_40s.npz', **result['output_dict'])
"""
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment, check=True)

        expected = json.loads((ROOT / "tests/fixtures/long_audio_expected.json").read_text())
        raw = np.load(ROOT / ".codex-tmp/raw_predictions_40s.npz")
        for key, expected_hash in expected.items():
            if key == "midi":
                actual_hash = hashlib.sha256(
                    (ROOT / ".codex-tmp/transcription_40s.mid").read_bytes()
                ).hexdigest().upper()
            else:
                actual_hash = hashlib.sha256(raw[key].tobytes()).hexdigest().upper()
            self.assertEqual(actual_hash, expected_hash, key)


if __name__ == "__main__":
    unittest.main()
