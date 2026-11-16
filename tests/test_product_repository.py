"""Contract tests for the separate v1 product database."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pianotrans_backend.artifacts import snapshot_file
from pianotrans_backend.product_store import ProductRepository, StaleRevision
from pianotrans_backend.product_contract import CommandError, CommandResult, EntryKind, EntryRef, Outcome


class ProductRepositoryTests(unittest.TestCase):
    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.db = self.root / "product-v1.db"
        self.audio_a = self.root / "a.wav"
        self.audio_b = self.root / "b.wav"
        self.audio_a.write_bytes(b"RIFF-a")
        self.audio_b.write_bytes(b"RIFF-b")
        self.store = ProductRepository(self.db)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_import_edit_reorder_and_restart_without_persisted_hash(self):
        first = self.store.import_audio([self.audio_a])["imported"][0]
        self.store.set_checked([first["taskId"]], False)
        second = self.store.import_audio([self.audio_b])["imported"][0]
        self.assertFalse(self.store.list_work_items()[0]["checked"])
        self.assertTrue(second["checked"])
        self.store.set_config(first["taskId"], {"batchSize": 8}, self.store.get_work_item(first["taskId"])["revision"])
        self.store.set_work_item_order([second["taskId"], first["taskId"]])
        self.store.close()
        self.store = ProductRepository(self.db)
        items = self.store.list_work_items()
        self.assertEqual([second["taskId"], first["taskId"]], [x["taskId"] for x in items])
        self.assertEqual([True, False], [x["checked"] for x in items])
        self.assertEqual(8, items[1]["batchSize"])
        self.assertNotIn("inputSha256", items[0])
        self.assertEqual(3, items[1]["revision"])

    def test_invalid_and_duplicate_paths_do_not_change_existing_rows(self):
        first = self.store.import_audio([self.audio_a])["imported"][0]
        self.store.set_checked([first["taskId"]], False)
        result = self.store.import_audio([self.audio_a, self.root / "bad.txt", self.root / "gone.wav"])
        self.assertEqual(0, len(result["imported"]))
        self.assertEqual(3, len(result["skipped"]))
        self.assertFalse(self.store.list_work_items()[0]["checked"])

    def test_import_rejects_same_content_in_work_and_after_history_deleted(self):
        self.store.import_audio([self.audio_a])
        duplicate = self.root / "same.wav"
        duplicate.write_bytes(self.audio_a.read_bytes())
        result = self.store.import_audio([duplicate])
        self.assertEqual("duplicate_content", result["skipped"][0]["code"])
        self.store.remove_checked_work_items()

        source = snapshot_file(self.audio_a)
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), str(self.root / "a.mid"),
                                  source.sha256, source.size_bytes, source.mtime_ns)
        self.assertEqual("already_active", self.store.import_audio([duplicate])["skipped"][0]["code"])
        self.store.finish_attempt("a1", "succeeded", output_sha256="b" * 64,
                                  output_size=4, output_mtime=5)
        self.store.delete_history(["a1"])
        result = self.store.import_audio([duplicate])
        self.assertEqual("already_transcribed", result["skipped"][0]["code"])
        self.assertEqual([], self.store.list_work_items())

    def test_reassign_unsubmitted_path_and_device_preference_survive_restart(self):
        item = self.store.import_audio([self.audio_a])["imported"][0]
        moved = self.root / "moved.wav"
        self.audio_a.rename(moved)
        updated = self.store.reassign_input(item["taskId"], moved, item["revision"])
        self.assertEqual(str(moved.resolve()), updated["inputPath"])
        self.assertNotIn("inputSha256", updated)
        self.store.set_setting("device_mode", "cpu")
        self.store.close()
        self.store = ProductRepository(self.db)
        self.assertEqual("cpu", self.store.get_setting("device_mode", "auto"))
        self.assertEqual(str(moved.resolve()), self.store.get_work_item(item["taskId"])["inputPath"])

    def test_batch_config_and_all_checked_include_items_after_first_page(self):
        paths = [self.root / f"audio-{number:03}.wav" for number in range(105)]
        for number, path in enumerate(paths):
            path.write_bytes(f"RIFF-{number}".encode())
        self.store.import_audio(paths)
        first = self.store.page_work_items(limit=100)
        self.assertEqual(105, first["totalCount"])
        self.assertIsNotNone(first["nextCursor"])
        second = self.store.page_work_items(limit=100, after=first["nextCursor"],
                                            query_token=first["queryToken"])
        self.assertEqual(5, len(second["items"]))
        self.assertEqual(105, self.store.set_checked_config({"batchSize": 8}, self.store.query_token()))
        self.assertEqual(8, self.store.get_work_item(second["items"][-1]["taskId"])["batchSize"])
        self.assertEqual(105, len(self.store.checked_work_revisions()))
        self.assertEqual(105, self.store.set_all_checked(False))
        self.assertEqual({}, self.store.checked_work_revisions())

    def test_numbered_work_pages_and_two_removal_scopes(self):
        paths = [self.root / f"page-{number:02}.wav" for number in range(45)]
        for number, path in enumerate(paths):
            path.write_bytes(f"RIFF-{number}".encode())
        self.store.import_audio(paths)
        first = self.store.numbered_work_page(1)
        second = self.store.numbered_work_page(2)
        last = self.store.numbered_work_page(3)
        self.assertEqual((20, 20, 5), tuple(len(page["items"]) for page in (first, second, last)))
        self.assertEqual(3, first["pageCount"])
        ids = [item["taskId"] for item in second["items"]]
        self.assertEqual(20, self.store.remove_checked_work_items(page_ids=ids,
                           query_token=second["queryToken"]))
        self.assertEqual(25, self.store.numbered_work_page(2)["totalCount"])
        self.assertEqual(25, self.store.remove_checked_work_items())
        self.assertEqual(0, self.store.numbered_work_page(1)["totalCount"])

    def test_stale_version_rejects_edit_and_reorder_is_exact(self):
        first = self.store.import_audio([self.audio_a])["imported"][0]
        self.store.set_config(first["taskId"], {"batchSize": 8}, first["revision"])
        with self.assertRaises(StaleRevision):
            self.store.set_config(first["taskId"], {"batchSize": 9}, first["revision"])
        with self.assertRaises(ValueError):
            self.store.set_work_item_order([])
        self.assertEqual(8, self.store.list_work_items()[0]["batchSize"])

    def test_database_constraints_require_input_and_success_output_snapshot(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), "x.mid", None, 2, 3)
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), "x.mid", "a" * 64, 2, 3)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish_attempt("a1", "succeeded")
        self.assertEqual("queued", self.store.get_attempt("a1")["status"])

    def test_history_clear_does_not_delete_active_attempt(self):
        self.store.create_attempt("active", "task", "submission", str(self.audio_a),
                                  str(self.root / "active.mid"), "a" * 64, 2, 3)
        preview = self.store.preview_delete_history()
        self.assertEqual([], preview["ids"])
        self.assertEqual(0, self.store.delete_history(["active"]))
        self.assertEqual("queued", self.store.get_attempt("active")["status"])

    def test_success_register_survives_visible_deletions(self):
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), str(self.root / "a.mid"), "a" * 64, 2, 3)
        self.store.finish_attempt("a1", "succeeded", output_sha256="b" * 64, output_size=4, output_mtime=5)
        favorite = self.store.add_favorite("a1")
        group = self.store.create_collection("练习")
        self.store.set_membership_delta([favorite["favoriteId"]], add=[group["collectionId"]])
        self.store.delete_collection(group["collectionId"])
        self.assertEqual(1, len(self.store.list_favorites()["items"]))
        self.assertEqual(1, len(self.store.list_favorites(collection="unclassified")["items"]))
        self.store.delete_history(["a1"])
        self.assertEqual(1, len(self.store.list_favorites()["items"]))
        self.store.remove_favorites([favorite["favoriteId"]])
        self.assertEqual(0, len(self.store.list_favorites()["items"]))
        record = self.store.find_success("a" * 64)
        self.assertEqual("a1", record["sourceAttemptId"])
        self.assertEqual(str(self.audio_a), record["inputPath"])
        self.assertEqual("b" * 64, record["outputSha256"])

    def test_favorite_search_uses_database_query(self):
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), "x.mid", "a" * 64, 2, 3)
        self.store.finish_attempt("a1", "succeeded", output_sha256="b" * 64, output_size=4, output_mtime=5)
        self.store.add_favorite("a1")
        self.assertEqual(1, self.store.list_favorites(query="a.wav")["totalCount"])
        self.assertEqual(0, self.store.list_favorites(query="missing.wav")["totalCount"])

    def test_pagination_and_corrupt_database_failure(self):
        for index in range(25):
            path = self.root / f"{index}.wav"
            path.write_bytes(str(index).encode())
            self.store.import_audio([path])
        page = self.store.list_work_items(limit=10)
        self.assertEqual(10, len(page))
        second_page = self.store.list_work_items(limit=10, after=(page[-1]["orderKey"], page[-1]["taskId"]))
        self.assertEqual(10, len(second_page))
        self.assertNotEqual(page[-1]["taskId"], second_page[0]["taskId"])
        self.assertEqual(25, len(self.store.list_work_items(limit=100)))
        self.store.close()
        self.db.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            ProductRepository(self.db)
        self.store = ProductRepository(self.root / "other.db")

    def test_product_v1_schema_upgrades_without_losing_work_item(self):
        self.store.close()
        legacy = self.root / "product-legacy.db"
        with sqlite3.connect(legacy) as db:
            db.executescript("""
                PRAGMA user_version=1;
                CREATE TABLE work_items (
                    task_id TEXT PRIMARY KEY, input_path TEXT NOT NULL UNIQUE,
                    output_rule TEXT NOT NULL, output_dir TEXT, midi_name TEXT NOT NULL,
                    batch_size INTEGER NOT NULL, order_key INTEGER NOT NULL,
                    checked INTEGER NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE history_attempts (
                    attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, submission_id TEXT NOT NULL,
                    input_path TEXT NOT NULL, midi_path TEXT NOT NULL, status TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL, input_size INTEGER NOT NULL, input_mtime INTEGER NOT NULL,
                    output_sha256 TEXT, output_size INTEGER, output_mtime INTEGER,
                    queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    error_code TEXT, error_message TEXT, revision INTEGER NOT NULL DEFAULT 0
                );
            """)
            db.execute("""INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       ("legacy-task", str(self.audio_a), "source_directory", None, "a.mid",
                        16, 1, 1, 0, "2026-01-01T00:00:00+00:00"))
        db.close()
        try:
            self.store = ProductRepository(legacy)
        except Exception as exc:
            raise AssertionError(f"v1 upgrade failed: {exc!r}") from exc
        item = self.store.get_work_item("legacy-task")
        self.assertEqual(str(self.audio_a), item["inputPath"])
        self.assertIsNone(item["recoveryRecordId"])
        self.assertEqual(2, self.store.db.execute("PRAGMA user_version").fetchone()[0])

    def test_collection_rename_and_failed_membership_roll_back(self):
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), "x.mid", "a" * 64, 2, 3)
        self.store.finish_attempt("a1", "succeeded", output_sha256="b" * 64, output_size=4, output_mtime=5)
        favorite = self.store.add_favorite("a1")
        group = self.store.create_collection("old")
        renamed = self.store.rename_collection(group["collectionId"], "new", 0)
        self.assertEqual(1, renamed["revision"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.set_membership_delta([favorite["favoriteId"]], add=[group["collectionId"], "missing"])
        self.assertEqual(1, len(self.store.list_favorites(collection="unclassified")["items"]))

    def test_duplicate_request_replays_without_creating_another_item(self):
        original = self.store.import_audio([self.audio_a], request_id="r-1")
        self.store.close()
        self.store = ProductRepository(self.db)
        self.assertEqual(original, self.store.import_audio([self.audio_a], request_id="r-1"))
        self.assertEqual(1, len(self.store.list_work_items()))
        with self.assertRaises(ValueError):
            self.store.import_audio([self.audio_b], request_id="r-1")

    def test_page_query_rejects_changes_between_pages(self):
        self.store.import_audio([self.audio_a])
        page = self.store.page_work_items(limit=1)
        self.assertEqual(1, page["totalCount"])
        self.store.import_audio([self.audio_b])
        with self.assertRaises(StaleRevision):
            self.store.page_work_items(limit=1, query_token=page["queryToken"])

    def test_confirmation_scope_does_not_include_new_history(self):
        self.store.create_attempt("a1", "t1", "s1", str(self.audio_a), "a.mid", "a" * 64, 2, 3)
        self.store.finish_attempt("a1", "failed")
        preview = self.store.preview_delete_history()
        self.store.create_attempt("a2", "t2", "s2", str(self.audio_b), "b.mid", "b" * 64, 2, 3)
        self.store.finish_attempt("a2", "failed")
        with self.assertRaises(ValueError):
            self.store.confirm_delete_history(preview["token"], ["a1", "a2"])
        result = self.store.confirm_delete_history(preview["token"], ["a1"])
        self.assertEqual(["a1"], result["deleted"])
        self.assertIsNotNone(self.store.get_attempt("a2"))
        with self.assertRaises(StaleRevision):
            self.store.confirm_delete_history(preview["token"], ["a1"])

    def test_duplicate_success_rolls_back_second_terminal_update(self):
        self.store.create_attempt("a1", "a1", "s1", str(self.audio_a), "a1.mid", "a" * 64, 2, 3)
        self.store.finish_attempt("a1", "succeeded", output_sha256="b" * 64, output_size=4, output_mtime=5)
        self.store.create_attempt("a2", "a2", "s1", str(self.audio_a), "a2.mid", "a" * 64, 2, 3)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish_attempt("a2", "succeeded", output_sha256="c" * 64, output_size=4, output_mtime=5)
        self.assertEqual("queued", self.store.get_attempt("a2")["status"])

    def test_contract_identity_and_rejected_error(self):
        ref = EntryRef(EntryKind.FAVORITE, "f1")
        result = CommandResult("r1", Outcome.REJECTED,
                               error=CommandError("file_missing", "文件不存在", subject=ref))
        self.assertEqual("favorite", result.error.subject.kind)
        with self.assertRaises(ValueError):
            CommandResult("r2", Outcome.REJECTED)


if __name__ == "__main__":
    unittest.main()
