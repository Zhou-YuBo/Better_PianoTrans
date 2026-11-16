"""On-demand verification and source-specific relocation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.product_contract import EntryKind, EntryRef
from pianotrans_backend.product_files import ProductFileService, FileResolutionError
from pianotrans_backend.product_store import ProductRepository


class ProductFileServiceTests(unittest.TestCase):
    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.store = ProductRepository(self.root / "product.db")
        self.files = ProductFileService(self.store)
        self.audio = self.root / "a.wav"
        self.midi = self.root / "a.mid"
        self.audio.write_bytes(b"RIFFsame")
        self.midi.write_bytes(b"MThdsame")
        a, m = snapshot_file(self.audio), snapshot_file(self.midi)
        self.store.create_attempt("a1", "t1", "s1", str(self.audio), str(self.midi), a.sha256, a.size_bytes, a.mtime_ns)
        self.store.finish_attempt("a1", "succeeded", output_sha256=m.sha256,
                                  output_size=m.size_bytes, output_mtime=m.mtime_ns)
        self.favorite = self.store.add_favorite("a1")["favoriteId"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_midi_only_checks_midi_and_favorite_survives_history_deletion(self):
        self.store.delete_history(["a1"])
        with patch("pianotrans_backend.product_files.verify_file", wraps=__import__(
                "pianotrans_backend.artifacts", fromlist=["verify_file"]).verify_file) as verifier:
            result = self.files.check(EntryRef(EntryKind.FAVORITE, self.favorite), "midi")
        self.assertEqual("match", result["checks"]["midi"])
        self.assertEqual(1, verifier.call_count)

    def test_relocating_favorite_does_not_change_history(self):
        moved = self.root / "moved.mid"
        self.midi.rename(moved)
        before = self.store.get_favorite(self.favorite)
        result = self.files.relocate(EntryRef(EntryKind.FAVORITE, self.favorite), "midi", moved, before["revision"])
        self.assertEqual(str(moved), result["midi_path"])
        self.assertEqual(str(self.midi), self.store.get_attempt("a1")["midi_path"])

    def test_mismatched_relocation_cannot_change_path(self):
        other = self.root / "other.mid"
        other.write_bytes(b"wrong")
        before = self.store.get_favorite(self.favorite)
        with self.assertRaises(FileResolutionError):
            self.files.relocate(EntryRef(EntryKind.FAVORITE, self.favorite), "midi", other, before["revision"])
        self.assertEqual(before["midi_path"], self.store.get_favorite(self.favorite)["midi_path"])


if __name__ == "__main__":
    unittest.main()
