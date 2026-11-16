from __future__ import annotations

import argparse
import json
import os
import sys
from time import perf_counter
from pathlib import Path
from typing import Callable

import torch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT = Path(os.environ.get("PIANOTRANS_CHECKPOINT", str(PROJECT_ROOT / "checkpoints" / "note_F1=0.9677_pedal_F1=0.9186.pth")))
os.environ.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".codex-cache" / "matplotlib"))
sys.path[:0] = [
    str(PROJECT_ROOT / "third_party" / "byte_dance_core" / "pytorch"),
    str(PROJECT_ROOT / "third_party" / "byte_dance_core" / "utils"),
]

from inference import PianoTranscription  # noqa: E402
from utilities import load_audio  # noqa: E402
from .devices import DeviceSelectionError, resolve_device


StatusCallback = Callable[[str], None]


def result(
    status: str,
    input_path: Path,
    midi_path: Path | None = None,
    error: dict | None = None,
    timings: dict[str, float] | None = None,
) -> dict:
    response = {
        "task_id": input_path.stem,
        "status": status,
        "progress": 100 if status == "succeeded" else 0,
        "input_path": str(input_path),
        "midi_path": str(midi_path) if midi_path else None,
        "error": error,
    }
    if timings is not None:
        response["timings"] = timings
    return response


def transcribe_one(
    input_path: Path,
    output_dir: Path,
    checkpoint: Path,
    overwrite: bool,
    batch_size: int = 1,
    status_callback: StatusCallback | None = None,
    midi_path: Path | None = None,
    device_mode: str = "auto",
) -> dict:
    def report(message: str) -> None:
        if status_callback is not None:
            status_callback(message)

    input_path = input_path.resolve()
    if not input_path.is_file():
        return result("failed", input_path, error={"code": "input_not_found", "message": "输入文件不存在。"})
    if not checkpoint.is_file():
        return result("failed", input_path, error={"code": "checkpoint_not_found", "message": "checkpoint 不存在。"})

    midi_path = midi_path.resolve() if midi_path is not None else output_dir.resolve() / f"{input_path.stem}.mid"
    if midi_path.exists() and not overwrite:
        return result("failed", input_path, error={"code": "output_exists", "message": "输出 MIDI 已存在。"})
    if batch_size < 1:
        return result("failed", input_path, error={"code": "invalid_batch_size", "message": "batch_size 必须大于 0。"})
    try:
        try:
            device, execution_device = resolve_device(device_mode)
        except DeviceSelectionError as exc:
            return result("failed", input_path, error={"code": exc.code, "message": str(exc)})
        except ValueError as exc:
            return result("failed", input_path, error={"code": "invalid_device", "message": str(exc)})
        started_at = perf_counter()
        report("正在解码音频...")
        decode_started_at = perf_counter()
        audio, sample_rate = load_audio(str(input_path), sr=16000, mono=True)
        decode_seconds = perf_counter() - decode_started_at
        if sample_rate != 16000 or audio.size == 0:
            raise ValueError("音频解码结果无效。")
        output_dir.mkdir(parents=True, exist_ok=True)
        report(f"正在加载模型...（{execution_device.upper()} 批大小：{batch_size}）")
        model_started_at = perf_counter()
        transcriptor = PianoTranscription(
            model_type="Note_pedal",
            checkpoint_path=str(checkpoint),
            device=device,
            post_processor_type="regression",
            batch_size=batch_size,
        )
        model_seconds = perf_counter() - model_started_at
        report(f"正在进行 {execution_device.upper()} 推理并写入 MIDI...")
        transcription_started_at = perf_counter()
        transcriptor.transcribe(audio, str(midi_path))
        transcription_seconds = perf_counter() - transcription_started_at
        report("MIDI 已写入。")
        response = result(
            "succeeded",
            input_path,
            midi_path=midi_path,
            timings={
                "decode_seconds": decode_seconds,
                "model_seconds": model_seconds,
                "transcription_seconds": transcription_seconds,
                "total_seconds": perf_counter() - started_at,
            },
        )
        response["input_duration_ms"] = int(round(audio.size * 1000 / sample_rate))
        response["input_sample_rate"] = sample_rate
        response["input_channels"] = 1
        response["device_mode"] = device_mode
        response["execution_device"] = execution_device
        return response
    except Exception as exc:
        return result(
            "failed",
            input_path,
            error={"code": "transcription_failed", "message": str(exc)},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="本地钢琴音频转 MIDI")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs" / "transcriptions")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    response = transcribe_one(
        args.input_path,
        args.output_dir,
        args.checkpoint,
        args.overwrite,
        batch_size=args.batch_size,
        device_mode=args.device,
    )
    print(json.dumps(response, ensure_ascii=False))
    return 0 if response["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
