"""Qt service-thread contract for the new product pipeline."""

import os
import tempfile
import time
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from pianotrans_gui.product_facade import WorkbenchFacade
from pianotrans_backend.cli import DEFAULT_CHECKPOINT, PROJECT_ROOT
from pianotrans_backend.artifacts import snapshot_file


def visible_items(item):
    for child in item.childItems():
        if child.isVisible():
            yield child
            yield from visible_items(child)


class ProductFacadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QQuickStyle.setStyle("Basic")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        (Path.cwd() / ".codex-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / ".codex-tmp")
        self.root = Path(self.temp.name)
        self.audio = self.root / "sample.wav"
        self.audio.write_bytes(b"RIFF-sample")
        self.facade = WorkbenchFacade(self.root / "product.db")
        self.results = []
        self.facade.commandFinished.connect(lambda request_id, name, result: self.results.append((request_id, name, result)))

    def tearDown(self):
        self.facade.close()
        self.temp.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 8
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition(), "Qt service did not answer before timeout")

    def test_commands_persist_in_product_database_without_input_hash(self):
        request_id = self.facade.command("importAudio", {"paths": [str(self.audio)]})
        self.wait_for(lambda: any(row[0] == request_id for row in self.results))
        self.assertTrue(self.results[-1][2]["ok"])
        self.wait_for(lambda: self.facade.projection["work"]["totalCount"] == 1)
        work = self.facade.projection["work"]["items"][0]
        self.assertTrue(work["checked"])
        self.assertNotIn("inputSha256", work)
        request_id = self.facade.command("setChecked", {"ids": [work["taskId"]], "checked": False})
        self.wait_for(lambda: any(row[0] == request_id for row in self.results))
        self.assertFalse(self.facade.projection["work"]["items"][0]["checked"])
        self.facade.close()
        self.facade = WorkbenchFacade(self.root / "product.db")
        self.wait_for(lambda: self.facade.projection["work"]["totalCount"] == 1)
        self.assertFalse(self.facade.projection["work"]["items"][0]["checked"])

    def test_work_page_and_both_remove_commands(self):
        paths = [self.root / f"audio-{i:02}.wav" for i in range(25)]
        for index, path in enumerate(paths):
            path.write_bytes(f"RIFF-{index}".encode())
        imported = self.facade.command("importAudio", {"paths": [str(path) for path in paths]})
        self.wait_for(lambda: any(row[0] == imported for row in self.results))
        self.assertEqual(20, len(self.facade.projection["work"]["items"]))
        page = self.facade.command("setWorkPage", {"page": 2})
        self.wait_for(lambda: any(row[0] == page for row in self.results))
        second = self.facade.projection["work"]
        self.assertEqual((2, 5), (second["page"], len(second["items"])))
        removed = self.facade.command("removeCheckedWork", {"scope": "page",
            "ids": [item["taskId"] for item in second["items"]], "queryToken": second["queryToken"]})
        self.wait_for(lambda: any(row[0] == removed for row in self.results))
        self.assertEqual(5, next(row[2]["data"] for row in self.results if row[0] == removed))
        self.assertEqual(20, self.facade.projection["work"]["totalCount"])
        removed_all = self.facade.command("removeCheckedWork", {"scope": "all"})
        self.wait_for(lambda: any(row[0] == removed_all for row in self.results))
        self.assertEqual(20, next(row[2]["data"] for row in self.results if row[0] == removed_all))
        self.assertEqual(0, self.facade.projection["work"]["totalCount"])

    def test_qml_row_selection_uses_new_facade_projection(self):
        request_id = self.facade.command("importAudio", {"paths": [str(self.audio)]})
        self.wait_for(lambda: any(row[0] == request_id for row in self.results))
        self.wait_for(lambda: self.facade.projection["work"]["totalCount"] == 1)
        task_id = self.facade.projection["work"]["items"][0]["taskId"]
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("facade", self.facade)
        engine.load(QUrl.fromLocalFile(str(PROJECT_ROOT / "src" / "pianotrans_gui" / "qml" / "Workbench.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        self.wait_for(lambda: window.property("width") > 0)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(180, 185))
        self.wait_for(lambda: window.property("selectedId") == task_id)
        self.assertEqual("task", window.property("selectedKind"))
        checkbox = next(item for item in visible_items(window.contentItem())
                        if item.metaObject().className() == "WorkCheck" and item.property("checked") is True)
        center = checkbox.mapToScene(QPointF(checkbox.width() / 2, checkbox.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))
        self.wait_for(lambda: not self.facade.projection["work"]["items"][0]["checked"])
        window.close()

    def test_qml_page_navigation_and_page_scoped_removal(self):
        paths = [self.root / f"page-{i:02}.wav" for i in range(25)]
        for index, path in enumerate(paths):
            path.write_bytes(f"RIFF-{index}".encode())
        request_id = self.facade.command("importAudio", {"paths": [str(path) for path in paths]})
        self.wait_for(lambda: any(row[0] == request_id for row in self.results))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("facade", self.facade)
        engine.load(QUrl.fromLocalFile(str(PROJECT_ROOT / "src" / "pianotrans_gui" / "qml" / "Workbench.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]

        def click_button(label):
            button = next(item for item in visible_items(window.contentItem())
                          if item.property("text") == label and item.metaObject().className() == "WorkButton")
            center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))

        click_button("下一页")
        self.wait_for(lambda: self.facade.projection["work"]["page"] == 2)
        self.assertEqual(5, len(self.facade.projection["work"]["items"]))
        click_button("移除本页勾选项")
        self.wait_for(lambda: self.facade.projection["work"]["totalCount"] == 20)
        self.assertEqual(20, self.facade.projection["work"]["checkedCount"])
        window.close()

    def test_qml_conflict_accepts_confirmed_overwrite(self):
        (self.root / "sample.mid").write_bytes(b"MThd external")
        imported = self.facade.command("importAudio", {"paths": [str(self.audio)]})
        self.wait_for(lambda: any(row[0] == imported for row in self.results))
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("facade", self.facade)
        engine.load(QUrl.fromLocalFile(str(PROJECT_ROOT / "src" / "pianotrans_gui" / "qml" / "Workbench.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]

        def click_button(label):
            button = next(item for item in visible_items(window.contentItem())
                          if item.property("text") == label and item.metaObject().className() == "WorkButton")
            center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))

        click_button("开始转录")
        self.wait_for(lambda: bool(window.property("submissionId")))
        self.assertTrue(any(item.property("text") == "另存编号"
                            for item in visible_items(window.contentItem())))
        click_button("覆盖现有 MIDI")
        self.wait_for(lambda: any(row[1] == "decideConflict" for row in self.results))
        self.assertTrue(next(row[2] for row in self.results if row[1] == "decideConflict")["ok"])
        self.wait_for(lambda: any(row[1] == "commitSubmission" for row in self.results))
        self.assertTrue(next(row[2] for row in self.results if row[1] == "commitSubmission")["ok"])
        self.assertEqual(b"MThd external", (self.root / "sample.mid").read_bytes())
        window.close()

    def test_settings_show_database_credit_and_persist_device(self):
        self.wait_for(lambda: bool(self.facade.projection.get("databasePath")))
        self.assertEqual(str((self.root / "product.db").resolve()),
                         self.facade.projection["databasePath"])
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("facade", self.facade)
        engine.load(QUrl.fromLocalFile(str(PROJECT_ROOT / "src" / "pianotrans_gui" / "qml" / "Workbench.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]

        def click_button(label):
            buttons = [item for item in visible_items(window.contentItem())
                       if item.metaObject().className() == "WorkButton"]
            button = next((item for item in buttons if item.property("text") == label), None)
            self.assertIsNotNone(button, [item.property("text") for item in buttons])
            center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))

        click_button("设置")
        self.wait_for(lambda: any(item.property("text") == "Better PianoTrans 设置"
                                  for item in visible_items(window.contentItem())))
        self.assertTrue(any("孔秋强" in str(item.property("text"))
                            for item in visible_items(window.contentItem())))
        self.assertTrue(any(str(self.root / "product.db") == item.property("text")
                            for item in visible_items(window.contentItem())))
        click_button("仅 CPU")
        self.wait_for(lambda: self.facade.projection["deviceMode"] == "cpu")
        click_button("完成")
        window.close()

    def test_settings_legal_entries_resolve_to_existing_files(self):
        for kind, expected in (("license", PROJECT_ROOT / "LICENSE"),
                               ("credits", PROJECT_ROOT / "third_party" / "byte_dance_core" / "NOTICE.md"),
                               ("third_party", PROJECT_ROOT / "third_party")):
            with self.subTest(kind=kind), patch("pianotrans_gui.product_facade.QDesktopServices.openUrl") as open_url:
                self.facade.openLegalFile(kind)
                open_url.assert_called_once()
                self.assertEqual(expected.resolve(), Path(open_url.call_args.args[0].toLocalFile()).resolve())

    @unittest.skipUnless(DEFAULT_CHECKPOINT.exists(), "checkpoint is unavailable")
    def test_qml_start_button_reaches_real_cpu_terminal_state(self):
        shutil.copyfile(PROJECT_ROOT / "tests" / "fixtures" / "liszt_10s_16k_mono.wav", self.audio)
        self.facade.command("setDevice", {"mode": "cpu"})
        request_id = self.facade.command("importAudio", {"paths": [str(self.audio)]})
        self.wait_for(lambda: any(row[0] == request_id for row in self.results))
        self.wait_for(lambda: self.facade.projection["work"]["checkedCount"] == 1)
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("facade", self.facade)
        engine.load(QUrl.fromLocalFile(str(PROJECT_ROOT / "src" / "pianotrans_gui" / "qml" / "Workbench.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        self.wait_for(lambda: window.property("width") > 0)
        start = next(item for item in window.findChildren(QQuickItem) if item.property("text") == "开始转录")
        center = start.mapToScene(QPointF(start.width() / 2, start.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))
        self.wait_for(lambda: any(row[1] == "commitSubmission" for row in self.results))
        deadline = time.monotonic() + 150
        rows = []
        while time.monotonic() < deadline:
            self.app.processEvents()
            rows = self.facade.projection["history"]["items"]
            if rows and rows[0]["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(.05)
        self.assertEqual("succeeded", rows[0]["status"], rows[0].get("error_message"))
        self.assertEqual(snapshot_file(Path(rows[0]["midi_path"])).sha256, rows[0]["output_sha256"])
        history_button = next(item for item in window.findChildren(QQuickItem)
                              if item.property("text") == "历史" and item.property("visible"))
        center = history_button.mapToScene(QPointF(history_button.width() / 2, history_button.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))
        self.wait_for(lambda: window.property("page") == "library")
        self.wait_for(lambda: window.property("selectedKind") == "history")
        self.assertEqual(rows[0]["attempt_id"], window.property("selectedId"))
        star = next(item for item in visible_items(window.contentItem())
                    if item.property("text") == "☆")
        center = star.mapToScene(QPointF(star.width() / 2, star.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(int(center.x()), int(center.y())))
        self.wait_for(lambda: self.facade.projection["favorites"]["totalCount"] == 1)
        favorite_id = self.facade.projection["favorites"]["items"][0]["favorite_id"]
        playing = self.facade.command("requestPlayback", {"kind": "history",
            "id": rows[0]["attempt_id"], "mode": "audio"})
        self.wait_for(lambda: any(result[0] == playing for result in self.results))
        self.wait_for(lambda: self.facade.playback["state"] == "playing")
        delete = self.facade.command("previewDeleteHistory", {"ids": [rows[0]["attempt_id"]]})
        self.wait_for(lambda: any(result[0] == delete for result in self.results))
        preview = next(result[2]["data"] for result in self.results if result[0] == delete)
        confirm = self.facade.command("confirmDeleteHistory", {"token": preview["token"], "ids": preview["ids"]})
        self.wait_for(lambda: any(result[0] == confirm for result in self.results))
        self.assertEqual("idle", self.facade.playback["state"])
        self.assertEqual(0, self.facade.projection["history"]["totalCount"])
        self.assertEqual(1, self.facade.projection["favorites"]["totalCount"])
        playback = self.facade.command("requestPlayback", {"kind": "favorite", "id": favorite_id, "mode": "midi"})
        self.wait_for(lambda: any(result[0] == playback for result in self.results))
        self.wait_for(lambda: self.facade.playback["state"] == "playing")
        window.close()

    @unittest.skipUnless(DEFAULT_CHECKPOINT.exists(), "checkpoint is unavailable")
    def test_real_cpu_flow_through_facade_and_product_database(self):
        shutil.copyfile(PROJECT_ROOT / "tests" / "fixtures" / "liszt_10s_16k_mono.wav", self.audio)

        def command(name, payload):
            request_id = self.facade.command(name, payload)
            self.wait_for(lambda: any(row[0] == request_id for row in self.results))
            result = next(row[2] for row in self.results if row[0] == request_id)
            self.assertTrue(result["ok"], result.get("error"))
            return result["data"]

        command("setDevice", {"mode": "cpu"})
        command("importAudio", {"paths": [str(self.audio)]})
        prepared = command("prepareSubmission", {})
        self.assertEqual("ready", prepared["items"][0]["status"])
        committed = command("commitSubmission", {"submissionId": prepared["submissionId"]})
        self.assertEqual(1, len(committed["queued"]))
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            self.app.processEvents()
            rows = self.facade.projection["history"]["items"]
            if rows and rows[0]["status"] in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(.05)
        self.assertEqual("succeeded", rows[0]["status"], rows[0].get("error_message"))
        self.assertEqual(snapshot_file(self.audio).sha256, rows[0]["input_sha256"])
        midi = Path(rows[0]["midi_path"])
        self.assertEqual(snapshot_file(midi).sha256, rows[0]["output_sha256"])
        import mido
        self.assertTrue(any(message.type == "note_on" for message in mido.MidiFile(midi)))
        command("requestPlayback", {"kind": "history", "id": rows[0]["attempt_id"], "mode": "midi"})
        deadline = time.monotonic() + 10
        while self.facade.playback["state"] == "preparing" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.02)
        self.assertEqual("playing", self.facade.playback["state"])
        self.assertGreater(self.facade.playback["durations"]["midi"], 0)
