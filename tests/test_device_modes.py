from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.cli import transcribe_one
from pianotrans_backend.devices import CudaUnavailableError, resolve_device


class DeviceResolutionTest(unittest.TestCase):
    def test_cpu_is_always_available(self) -> None:
        device, actual = resolve_device("cpu")
        self.assertEqual(str(device), "cpu")
        self.assertEqual(actual, "cpu")

    @patch("pianotrans_backend.devices.torch.cuda.is_available", return_value=False)
    def test_auto_uses_cpu_without_cuda(self, _available) -> None:
        device, actual = resolve_device("auto")
        self.assertEqual(str(device), "cpu")
        self.assertEqual(actual, "cpu")

    @patch("pianotrans_backend.devices.torch.cuda.is_available", return_value=True)
    def test_auto_uses_cuda_when_available(self, _available) -> None:
        device, actual = resolve_device("auto")
        self.assertEqual(str(device), "cuda")
        self.assertEqual(actual, "cuda")

    @patch("pianotrans_backend.devices.torch.cuda.is_available", return_value=False)
    def test_explicit_cuda_does_not_fallback(self, _available) -> None:
        with self.assertRaises(CudaUnavailableError):
            resolve_device("cuda")

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_device("rocm")


class CliDeviceModeTest(unittest.TestCase):
    def test_cpu_mode_passes_cpu_to_transcriptor(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            checkpoint = root / "checkpoint.pth"
            input_path.write_bytes(b"audio")
            checkpoint.write_bytes(b"checkpoint")
            fake = Mock()
            with patch("pianotrans_backend.cli.load_audio", return_value=(np.zeros(16000, dtype=np.float32), 16000)), \
                patch("pianotrans_backend.cli.PianoTranscription", return_value=fake) as transcriptor_cls, \
                patch("pianotrans_backend.cli.torch.cuda.is_available", return_value=False):
                response = transcribe_one(input_path, root / "out", checkpoint, True, device_mode="cpu")
            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(response["execution_device"], "cpu")
            self.assertEqual(transcriptor_cls.call_args.kwargs["device"], torch.device("cpu"))

    def test_explicit_cuda_returns_structured_error_without_model(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp") as directory:
            root = Path(directory)
            input_path = root / "input.wav"
            checkpoint = root / "checkpoint.pth"
            input_path.write_bytes(b"audio")
            checkpoint.write_bytes(b"checkpoint")
            with patch("pianotrans_backend.cli.torch.cuda.is_available", return_value=False), \
                patch("pianotrans_backend.cli.PianoTranscription") as transcriptor:
                response = transcribe_one(input_path, root / "out", checkpoint, True, device_mode="cuda")
            self.assertEqual(response["error"]["code"], "cuda_unavailable")
            transcriptor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
