"""Service contract tests with a deterministic worker."""

import tempfile
import unittest
import shutil
from pathlib import Path
from time import monotonic, sleep

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.product_store import ProductRepository, StaleRevision
from pianotrans_backend.scheduler import WorkerResult
from pianotrans_backend.submission_service import SubmissionService
from pianotrans_backend.cli import DEFAULT_CHECKPOINT, PROJECT_ROOT
from pianotrans_backend.worker import ProcessWorkerStarter


class FakeHandle:
    def __init__(self, task):
        self.task = task
        self.terminated = False
        self.ready = False

    def poll(self):
        if not self.ready:
            return None
        self.task.midi_path.write_bytes(b"MThd" + self.task.task_id.encode())
        return WorkerResult("succeeded", midi_path=self.task.midi_path,
                            input_snapshot=snapshot_file(self.task.input_path),
                            output_snapshot=snapshot_file(self.task.midi_path))

    def terminate(self):
        self.terminated = True


class FakeStarter:
    def __init__(self):
        self.handles = []

    def start(self, task):
        handle = FakeHandle(task)
        self.handles.append(handle)
        return handle


class SubmissionServiceTests(unittest.TestCase):
    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.db = self.root / "product.db"
        self.store = ProductRepository(self.db)
        self.starter = FakeStarter()
        self.service = SubmissionService(self.store, self.starter)

    def tearDown(self):
        self.service.stop_all()
        self.store.close()
        self.temp.cleanup()

    def audio(self, name, content=b"RIFF-a"):
        path = self.root / name
        path.write_bytes(content)
        return self.store.import_audio([path])["imported"][0]

    def prepare(self, *items):
        return self.service.prepare_submission({item["taskId"]: item["revision"] for item in items})

    def test_same_content_is_rejected_on_import_and_after_success(self):
        a = self.audio("a.wav")
        duplicate = self.root / "b.wav"
        duplicate.write_bytes(b"RIFF-a")
        self.assertEqual("duplicate_content", self.store.import_audio([duplicate])["skipped"][0]["code"])
        session = self.prepare(a)
        self.assertEqual(["ready"], [x["status"] for x in session["items"]])
        committed = self.service.commit_submission(session["submissionId"])
        self.assertEqual(1, len(committed["queued"]))
        self.starter.handles[0].ready = True
        self.service.tick()
        self.assertEqual("succeeded", self.store.get_attempt(committed["queued"][0])["status"])
        self.store.delete_history(committed["queued"])
        self.assertEqual("already_transcribed", self.store.import_audio([duplicate])["skipped"][0]["code"])

    def test_prepared_source_change_rejects_commit(self):
        a = self.audio("a.wav")
        session = self.prepare(a)
        (self.root / "a.wav").write_bytes(b"changed")
        with self.assertRaises(StaleRevision):
            self.service.commit_submission(session["submissionId"])
        self.assertEqual([], self.store.list_history()["items"])
        self.assertEqual(1, len(self.store.list_work_items()))

    def test_external_target_creation_rejects_commit_and_rename_uses_free_name(self):
        a = self.audio("a.wav")
        session = self.prepare(a)
        (self.root / "a.mid").write_bytes(b"external")
        with self.assertRaises(StaleRevision):
            self.service.commit_submission(session["submissionId"])
        session = self.prepare(a)
        self.assertEqual("conflict", session["items"][0]["status"])
        self.service.decide_conflict(session["submissionId"], a["taskId"], "rename")
        committed = self.service.commit_submission(session["submissionId"])
        self.assertEqual(1, len(committed["queued"]))
        self.assertEqual("external", (self.root / "a.mid").read_text())
        self.assertEqual("a (1).mid", Path(self.store.get_attempt(committed["queued"][0])["midi_path"]).name)

    def test_conflict_origin_uses_output_hash_and_preview_rechecks_target(self):
        source = self.audio("registered.wav", b"registered audio")
        prior = self.prepare(source)
        attempt = self.service.commit_submission(prior["submissionId"])["queued"][0]
        self.starter.handles[0].ready = True
        self.service.tick()
        previous_midi = self.root / "registered.mid"
        another = self.audio("another.wav", b"different audio")
        self.store.set_config(another["taskId"], {"midiFileName": "registered.mid"}, another["revision"])
        another = self.store.get_work_item(another["taskId"])
        session = self.prepare(another)
        conflict = session["items"][0]
        self.assertEqual("conflict", conflict["status"])
        self.assertEqual("registered.wav", conflict["conflictOrigin"]["inputFileName"])
        self.assertEqual(str(previous_midi), self.service.preview_conflict_midi(
            session["submissionId"], another["taskId"])["path"])
        previous_midi.write_bytes(b"MThd replaced")
        with self.assertRaises(StaleRevision):
            self.service.preview_conflict_midi(session["submissionId"], another["taskId"])

    def test_unknown_conflict_has_no_origin(self):
        item = self.audio("unknown.wav", b"unique audio")
        (self.root / "unknown.mid").write_bytes(b"MThd external")
        session = self.prepare(item)
        self.assertEqual("conflict", session["items"][0]["status"])
        self.assertNotIn("conflictOrigin", session["items"][0])

    def test_batch_conflict_policy_applies_only_to_current_submission(self):
        a = self.audio("a.wav", b"one")
        b = self.audio("b.wav", b"two")
        (self.root / "a.mid").write_bytes(b"existing a")
        (self.root / "b.mid").write_bytes(b"existing b")
        session = self.prepare(a, b)
        result = self.service.decide_conflict(session["submissionId"], a["taskId"], "rename", True)
        self.assertEqual(["ready", "ready"], [item["status"] for item in result["items"]])
        self.assertEqual(["a (1).mid", "b (1).mid"],
                         [Path(item["midiPath"]).name for item in result["items"]])
        self.service.abandon_submission(session["submissionId"])
        again = self.prepare(a, b)
        self.assertEqual(["conflict", "conflict"], [item["status"] for item in again["items"]])

    def test_skip_retains_work_item_and_queue_is_serial(self):
        a = self.audio("a.wav", b"one")
        b = self.audio("b.wav", b"two")
        c = self.audio("c.wav", b"three")
        (self.root / "b.mid").write_bytes(b"existing")
        session = self.prepare(a, b, c)
        self.service.decide_conflict(session["submissionId"], b["taskId"], "skip")
        committed = self.service.commit_submission(session["submissionId"])
        self.assertEqual(2, len(committed["queued"]))
        self.assertEqual(1, len(self.starter.handles))
        self.assertEqual(1, len(self.store.list_work_items()))
        self.starter.handles[0].ready = True
        self.service.tick()
        self.assertEqual(2, len(self.starter.handles))

    def test_cancel_running_ignores_late_success(self):
        a = self.audio("a.wav")
        session = self.prepare(a)
        attempt = self.service.commit_submission(session["submissionId"])["queued"][0]
        handle = self.starter.handles[0]
        self.assertTrue(self.service.cancel_attempt(attempt))
        handle.ready = True
        self.service.tick()
        self.assertTrue(handle.terminated)
        self.assertEqual("cancelled", self.store.get_attempt(attempt)["status"])

    def test_waiting_reorder_changes_next_worker_and_persists_order(self):
        works = [self.audio(f"{name}.wav", name.encode()) for name in ("a", "b", "c")]
        session = self.prepare(*works)
        attempts = self.service.commit_submission(session["submissionId"])["queued"]
        revisions = {attempt: self.store.get_attempt(attempt)["revision"] for attempt in attempts[1:]}
        self.service.reorder_queued([attempts[2], attempts[1]], revisions)
        self.assertLess(self.store.get_attempt(attempts[2])["queue_order"],
                        self.store.get_attempt(attempts[1])["queue_order"])
        self.starter.handles[0].ready = True
        self.service.tick()
        self.assertEqual(attempts[2], self.starter.handles[1].task.task_id)
        with self.assertRaises(ValueError):
            self.service.reorder_queued([attempts[0], attempts[1]], revisions)

    def test_restart_marks_unfinished_attempt_as_interrupted(self):
        a = self.audio("a.wav")
        session = self.prepare(a)
        attempt = self.service.commit_submission(session["submissionId"])["queued"][0]
        self.store.close()
        self.store = ProductRepository(self.db)
        self.service = SubmissionService(self.store, FakeStarter())
        self.assertEqual(1, self.service.recovered)
        self.assertEqual("failed", self.store.get_attempt(attempt)["status"])
        self.assertEqual("app_interrupted", self.store.get_attempt(attempt)["error_code"])

    @unittest.skipUnless(DEFAULT_CHECKPOINT.exists(), "checkpoint is unavailable")
    def test_real_cpu_sample_records_matching_midi_snapshot(self):
        source = self.root / "real.wav"
        shutil.copyfile(PROJECT_ROOT / "tests" / "fixtures" / "liszt_10s_16k_mono.wav", source)
        work = self.store.import_audio([source])["imported"][0]
        self.service = SubmissionService(self.store, ProcessWorkerStarter(DEFAULT_CHECKPOINT, PROJECT_ROOT))
        session = self.service.prepare_submission({work["taskId"]: work["revision"]}, device_mode="cpu")
        attempt = self.service.commit_submission(session["submissionId"])["queued"][0]
        deadline = monotonic() + 150
        while self.store.get_attempt(attempt)["status"] == "running" and monotonic() < deadline:
            self.service.tick()
            sleep(0.05)
        row = self.store.get_attempt(attempt)
        self.assertEqual("succeeded", row["status"], row["error_message"])
        self.assertEqual("cpu", row["device_mode"])
        midi = Path(row["midi_path"])
        self.assertEqual(snapshot_file(midi).sha256, row["output_sha256"])
        self.assertEqual(snapshot_file(source).sha256, row["input_sha256"])
        self.assertEqual(row["output_sha256"], self.store.find_success(row["input_sha256"])["outputSha256"])
        import mido
        self.assertTrue(any(message.type == "note_on" for message in mido.MidiFile(midi)))


if __name__ == "__main__":
    unittest.main()
