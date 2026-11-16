from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.artifacts import ArtifactVerification, snapshot_file, verify_file


class ArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp")
        self.root = Path(self.tempdir.name)
        self.path = self.root / "音频.mp3"
        self.path.write_bytes(b"pianotrans-audio")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_snapshot_and_match(self) -> None:
        snapshot = snapshot_file(self.path)
        result = verify_file(self.path, snapshot.sha256, snapshot.size_bytes, snapshot.mtime_ns)
        self.assertEqual(result.status, ArtifactVerification.MATCH)

    def test_same_name_replacement_is_mismatch(self) -> None:
        snapshot = snapshot_file(self.path)
        self.path.write_bytes(b"different-audio")
        result = verify_file(self.path, snapshot.sha256, snapshot.size_bytes, snapshot.mtime_ns)
        self.assertEqual(result.status, ArtifactVerification.MISMATCH)

    def test_missing_and_unverified(self) -> None:
        snapshot = snapshot_file(self.path)
        self.path.unlink()
        self.assertEqual(verify_file(self.path, snapshot.sha256).status, ArtifactVerification.MISSING)
        self.path.write_bytes(b"restored")
        self.assertEqual(verify_file(self.path, None).status, ArtifactVerification.UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
