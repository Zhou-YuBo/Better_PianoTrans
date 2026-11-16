"""Device mode validation and resolution for transcription runs."""

from __future__ import annotations

from typing import Literal

import torch


DeviceMode = Literal["auto", "cpu", "cuda"]
DEVICE_MODES = frozenset(("auto", "cpu", "cuda"))


class DeviceSelectionError(RuntimeError):
    """Raised when the requested execution device cannot be selected."""

    code = "device_selection_failed"


class CudaUnavailableError(DeviceSelectionError):
    code = "cuda_unavailable"


def validate_device_mode(mode: str) -> DeviceMode:
    if mode not in DEVICE_MODES:
        raise ValueError("device must be one of: auto, cpu, cuda")
    return mode  # type: ignore[return-value]


def resolve_device(mode: str = "auto") -> tuple[torch.device, str]:
    """Resolve a requested mode without fallback after runtime startup."""
    requested = validate_device_mode(mode)
    if requested == "cpu":
        return torch.device("cpu"), "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise CudaUnavailableError("未检测到可用的 NVIDIA CUDA 设备。")
    if requested == "auto" and not torch.cuda.is_available():
        return torch.device("cpu"), "cpu"
    return torch.device("cuda"), "cuda"
