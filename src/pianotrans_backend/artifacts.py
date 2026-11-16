"""File snapshots and integrity checks for persisted transcription artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


CHUNK_SIZE = 1024 * 1024


class ArtifactVerification(StrEnum):
    MATCH = "match"
    MISSING = "missing"
    MISMATCH = "mismatch"
    UNVERIFIED = "unverified"
    UNREADABLE = "unreadable"


class ArtifactSnapshotError(RuntimeError):
    """Raised when a file changes while its snapshot is being captured."""


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    sha256: str
    size_bytes: int
    mtime_ns: int
    file_id: int = 0


@dataclass(frozen=True)
class VerificationResult:
    status: ArtifactVerification
    path: str
    actual_sha256: str | None = None
    message: str = ""


def snapshot_file(path: Path, retries: int = 1) -> FileSnapshot:
    """Hash one file and reject a file that changes during the read."""
    resolved = path.resolve(strict=False)
    for attempt in range(retries + 1):
        try:
            before = resolved.stat()
            if not resolved.is_file():
                raise FileNotFoundError(str(resolved))
            digest = hashlib.sha256()
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
            after = resolved.stat()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactSnapshotError(f"无法读取文件：{resolved}") from exc
        if (before.st_size, before.st_mtime_ns, before.st_ino) == (
                after.st_size, after.st_mtime_ns, after.st_ino):
            return FileSnapshot(str(resolved), digest.hexdigest(), after.st_size, after.st_mtime_ns, after.st_ino)
        if attempt == retries:
            raise ArtifactSnapshotError(f"文件在读取期间发生变化：{resolved}")
    raise AssertionError("unreachable")


def verify_file(
    path: Path,
    expected_sha256: str | None,
    expected_size_bytes: int | None = None,
    expected_mtime_ns: int | None = None,
) -> VerificationResult:
    """Verify a persisted snapshot without changing the stored record."""
    resolved = path.resolve(strict=False)
    if not resolved.exists():
        return VerificationResult(ArtifactVerification.MISSING, str(resolved), message="文件不存在")
    if not resolved.is_file():
        return VerificationResult(ArtifactVerification.UNREADABLE, str(resolved), message="路径不是文件")
    if not expected_sha256:
        return VerificationResult(ArtifactVerification.UNVERIFIED, str(resolved), message="历史记录没有文件指纹")
    try:
        current = snapshot_file(resolved)
    except FileNotFoundError:
        return VerificationResult(ArtifactVerification.MISSING, str(resolved), message="文件不存在")
    except ArtifactSnapshotError as exc:
        return VerificationResult(ArtifactVerification.MISMATCH, str(resolved), message=str(exc))
    except OSError:
        return VerificationResult(ArtifactVerification.UNREADABLE, str(resolved), message="文件无法读取")
    if current.sha256 != expected_sha256:
        return VerificationResult(
            ArtifactVerification.MISMATCH,
            str(resolved),
            actual_sha256=current.sha256,
            message="文件内容已经变化",
        )
    return VerificationResult(ArtifactVerification.MATCH, str(resolved), actual_sha256=current.sha256)
