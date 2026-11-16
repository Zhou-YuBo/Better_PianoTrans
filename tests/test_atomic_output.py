from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pianotrans_backend.output import AtomicMidiOutput, OutputCommitError


class AtomicMidiOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".codex-tmp")
        self.directory = Path(self.tempdir.name)
        self.final = self.directory / "夜曲.mid"
        self.output = AtomicMidiOutput(self.final, "task-123")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_temp_file_stays_next_to_final_and_commits_atomically(self) -> None:
        self.output.temporary_path.write_bytes(b"midi")
        self.output.commit(overwrite=False)
        self.assertEqual(self.final.read_bytes(), b"midi")
        self.assertFalse(self.output.temporary_path.exists())
        self.assertEqual(self.output.temporary_path.parent, self.final.parent)

    def test_existing_final_is_never_overwritten_without_safe_identity_proof(self) -> None:
        self.final.write_bytes(b"old")
        self.output.temporary_path.write_bytes(b"new")
        with self.assertRaises(OutputCommitError):
            self.output.commit(overwrite=False)
        self.assertEqual(self.final.read_bytes(), b"old")
        with self.assertRaises(OutputCommitError):
            self.output.commit(overwrite=True)
        self.assertEqual(self.final.read_bytes(), b"old")

    def test_external_creation_before_commit_keeps_external_bytes(self) -> None:
        self.output.temporary_path.write_bytes(b"new")
        self.final.write_bytes(b"external")
        with self.assertRaises(OutputCommitError):
            self.output.commit(overwrite=False)
        self.assertEqual(self.final.read_bytes(), b"external")

    def test_cleanup_only_removes_its_own_temp_file(self) -> None:
        unrelated = self.directory / ".other.pianotrans.tmp"
        unrelated.write_bytes(b"keep")
        self.output.temporary_path.write_bytes(b"remove")
        self.output.cleanup()
        self.assertFalse(self.output.temporary_path.exists())
        self.assertTrue(unrelated.exists())

    def test_commit_failure_leaves_no_final_midi(self) -> None:
        self.output.temporary_path.write_bytes(b"midi")
        with patch("pianotrans_backend.output.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OutputCommitError):
                self.output.commit(overwrite=False)
        self.assertFalse(self.final.exists())
        self.assertTrue(self.output.temporary_path.exists())


if __name__ == "__main__":
    unittest.main()
