"""Safe temporary MIDI writing and atomic final-path commits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .artifacts import ArtifactSnapshotError, FileSnapshot, snapshot_file


class OutputCommitError(RuntimeError):
    """Raised when the requested final MIDI cannot be safely committed."""


@dataclass(frozen=True)
class AtomicMidiOutput:
    final_path: Path
    task_id: str

    @property
    def temporary_path(self) -> Path:
        return self.final_path.with_name(f".{self.final_path.name}.{self.task_id}.pianotrans.tmp")

    def prepare(self) -> Path:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.cleanup()
        return self.temporary_path

    def commit(self, overwrite: bool, expected_target: FileSnapshot | None = None) -> None:
        if not self.temporary_path.is_file():
            raise OutputCommitError("临时 MIDI 文件不存在")
        if overwrite:
            if expected_target is None:
                raise OutputCommitError("缺少本次覆盖的目标指纹")
            try:
                current = snapshot_file(self.final_path)
            except (OSError, ArtifactSnapshotError) as exc:
                raise OutputCommitError("同名 MIDI 已变化，未覆盖") from exc
            if current != expected_target:
                raise OutputCommitError("同名 MIDI 已变化，未覆盖")
            try:
                os.replace(self.temporary_path, self.final_path)
            except OSError as exc:
                raise OutputCommitError("无法替换同名 MIDI") from exc
            return
        try:
            os.link(self.temporary_path, self.final_path)
        except FileExistsError as exc:
            raise OutputCommitError("目标位置已有同名 MIDI") from exc
        except OSError as exc:
            raise OutputCommitError("无法完成 MIDI 文件写入") from exc
        self.temporary_path.unlink(missing_ok=True)

    def cleanup(self) -> None:
        try:
            self.temporary_path.unlink()
        except FileNotFoundError:
            return
