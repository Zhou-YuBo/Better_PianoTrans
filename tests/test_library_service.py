"""Independent favorite and explicit result recovery contracts."""

import tempfile
import unittest
from pathlib import Path

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.library_service import LibraryService, RecoveryError
from pianotrans_backend.product_store import ProductRepository, StaleRevision
from pianotrans_backend.submission_service import SubmissionService
from tests.test_submission_service import FakeStarter


class LibraryServiceTests(unittest.TestCase):
    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.store = ProductRepository(self.root / "product.db")
        self.library = LibraryService(self.store)
        self.audio = self.root / "a.wav"
        self.midi = self.root / "a.mid"
        self.audio.write_bytes(b"RIFForiginal")
        self.midi.write_bytes(b"MThdoriginal")
        source, output = snapshot_file(self.audio), snapshot_file(self.midi)
        self.store.create_attempt("a1", "t1", "s1", str(self.audio), str(self.midi),
                                  source.sha256, source.size_bytes, source.mtime_ns)
        self.store.finish_attempt("a1", "succeeded", output_sha256=output.sha256,
                                  output_size=output.size_bytes, output_mtime=output.mtime_ns)
        self.success = self.store.find_success(source.sha256)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_deleted_history_and_favorite_do_not_remove_success_fact(self):
        favorite = self.store.add_favorite("a1")
        self.store.delete_history(["a1"])
        self.assertEqual("match", self.library.inspect_successful_result(self.success["successRecordId"])["outputStatus"])
        self.assertEqual(1, len(self.store.list_favorites()["items"]))
        self.store.remove_favorites([favorite["favoriteId"]])
        checked = self.library.inspect_successful_result(self.success["successRecordId"])
        self.assertEqual([], checked["visibleEntries"])
        self.assertEqual("match", checked["outputStatus"])
        with self.assertRaises(RecoveryError):
            self.library.begin_result_recovery(self.success["successRecordId"], self.success["revision"])

    def test_missing_result_requires_one_use_token_and_keeps_source_hash(self):
        self.store.delete_history(["a1"])
        self.midi.unlink()
        checked = self.library.inspect_successful_result(self.success["successRecordId"])
        self.assertEqual("missing", checked["outputStatus"])
        token = self.library.begin_result_recovery(self.success["successRecordId"], self.success["revision"])
        work = self.library.create_recovery_work_item(token)
        self.assertEqual(self.success["successRecordId"], work["recoveryRecordId"])
        with self.assertRaises(RecoveryError):
            self.library.create_recovery_work_item(token)
        self.assertEqual("a.mid", work["midiFileName"])

    def test_changed_source_blocks_recovery_and_matched_relocation_allows_it(self):
        self.midi.unlink()
        moved = self.root / "moved.wav"
        self.audio.rename(moved)
        with self.assertRaises(RecoveryError):
            self.library.begin_result_recovery(self.success["successRecordId"], self.success["revision"])
        self.library.relocate_success_input(self.success["successRecordId"], moved, self.success["revision"])
        revised = self.store.find_success(self.success["inputSha256"])
        token = self.library.begin_result_recovery(revised["successRecordId"], revised["revision"])
        work = self.library.create_recovery_work_item(token)
        self.assertEqual(str(moved), work["inputPath"])

    def test_nonmatching_relocation_keeps_registered_path(self):
        other = self.root / "other.wav"
        other.write_bytes(b"RIFFdifferent")
        with self.assertRaises(RecoveryError):
            self.library.relocate_success_input(self.success["successRecordId"], other, self.success["revision"])
        self.assertEqual(str(self.audio), self.store.find_success(self.success["inputSha256"])["inputPath"])

    def test_recovery_submission_replaces_result_metadata_once(self):
        self.midi.unlink()
        token = self.library.begin_result_recovery(self.success["successRecordId"], self.success["revision"])
        work = self.library.create_recovery_work_item(token)
        starter = FakeStarter()
        service = SubmissionService(self.store, starter)
        session = service.prepare_submission({work["taskId"]: work["revision"]})
        attempt = service.commit_submission(session["submissionId"])["queued"][0]
        starter.handles[0].ready = True
        service.tick()
        self.assertEqual("succeeded", self.store.get_attempt(attempt)["status"])
        updated = self.store.find_success(self.success["inputSha256"])
        self.assertEqual(self.success["successRecordId"], updated["successRecordId"])
        self.assertEqual(attempt, updated["sourceAttemptId"])
        self.assertEqual(snapshot_file(self.midi).sha256, updated["outputSha256"])

    def test_mixed_requeue_only_creates_failed_work_item(self):
        other = self.root / "failed.wav"
        other.write_bytes(b"RIFFfailed")
        source = snapshot_file(other)
        self.store.create_attempt("a2", "t2", "s2", str(other), str(self.root / "failed.mid"),
                                  source.sha256, source.size_bytes, source.mtime_ns, batch_size=8)
        self.store.finish_attempt("a2", "failed", error_code="worker_exited")
        preview = self.library.preview_requeue(["a1", "a2"])
        self.assertEqual(["a2"], preview["eligible"])
        self.assertEqual(["a1"], preview["ineligible"])
        created = self.library.requeue_eligible(preview["token"], ["a2"])
        self.assertEqual(1, len(created))
        self.assertEqual(8, created[0]["batchSize"])
        self.assertNotEqual("t2", created[0]["taskId"])
        self.assertEqual("failed", self.store.get_attempt("a2")["status"])
        with self.assertRaises(RecoveryError):
            self.library.requeue_eligible(preview["token"], ["a2"])


if __name__ == "__main__":
    unittest.main()
