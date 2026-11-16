"""Thread-bound product service and the read-only QML facade."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtQml import QJSValue
from PySide6.QtWidgets import QFileDialog

from pianotrans_backend.cli import DEFAULT_CHECKPOINT, PROJECT_ROOT
from pianotrans_backend.devices import resolve_device
from pianotrans_backend.product_contract import EntryKind, EntryRef
from pianotrans_backend.product_files import ProductFileService
from pianotrans_backend.product_playback import ProductPlaybackService
from pianotrans_backend.product_store import ProductRepository
from pianotrans_backend.library_service import LibraryService
from pianotrans_backend.submission_service import SubmissionService
from pianotrans_backend.worker import ProcessWorkerStarter

from .playback import QtAudioBackend
from pianotrans_backend.midi_worker import MidiWorkerClient


class _MidiBackend(QObject):
    received = Signal(object)

    def __init__(self, soundfont: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = MidiWorkerClient(soundfont)
        self.received.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self.client.set_event_callback(self.received.emit)
        self._callback = None

    def set_event_callback(self, callback) -> None:
        self._callback = callback

    @Slot(object)
    def _deliver(self, event: dict) -> None:
        if self._callback:
            self._callback(event)

    def prepare(self, path: str) -> bool:
        return self.client.prepare(path)

    def start(self) -> None:
        self.client.start()

    def pause(self) -> None:
        self.client.pause()

    def stop(self) -> None:
        self.client.stop()

    def seek(self, seconds: float) -> None:
        self.client.seek(seconds)

    def set_volume(self, value: float) -> None:
        self.client.set_volume(value)

    def close(self) -> None:
        self.client.close(force=True)


class _ProductWorker(QObject):
    finished = Signal(str, str, object)
    projection = Signal(object)
    failed = Signal(str)

    def __init__(self, db_path: Path | None) -> None:
        super().__init__()
        self.db_path = db_path
        self.store = None
        self.submission = None
        self.library = None
        self.files = None
        self.timer = None
        self._revision = -1
        self._history_status = "all"
        self._history_query = ""
        self._favorite_sort = "favorited"
        self._favorite_collection = "all"
        self._favorite_query = ""
        self._work_page = 1
        self._detected_device = "cpu"

    @Slot()
    def initialize(self) -> None:
        try:
            self.store = ProductRepository(self.db_path)
            self._detected_device = resolve_device("auto")[1]
            self.submission = SubmissionService(self.store, ProcessWorkerStarter(DEFAULT_CHECKPOINT, PROJECT_ROOT))
            self.library = LibraryService(self.store)
            self.files = ProductFileService(self.store)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(100)
            self._publish()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _publish(self) -> None:
        store = self.store
        token = store.query_token()
        history = store.list_history(status=self._history_status, query=self._history_query, limit=100)
        favorites = store.list_favorites(sort=self._favorite_sort, collection=self._favorite_collection,
                                         query=self._favorite_query, limit=100)
        active = store.db.execute("""SELECT * FROM history_attempts WHERE status IN ('queued','running')
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,queue_order,attempt_id""").fetchall()
        work = store.numbered_work_page(self._work_page)
        self._work_page = work["page"]
        self.projection.emit({
            "work": work,
            "active": [dict(row) for row in active],
            "history": history,
            "favorites": favorites,
            "collections": store.list_collections(),
            "deviceMode": store.get_setting("device_mode", "auto"),
            "detectedDevice": self._detected_device,
            "databasePath": str(store.path.resolve(strict=False)),
            "appVersion": os.environ.get("PIANOTRANS_APP_VERSION", "0.1.0-dev"),
            "revision": token,
        })
        self._revision = token

    @Slot()
    def _tick(self) -> None:
        self.submission.tick()
        if self.store.query_token() != self._revision:
            self._publish()

    @Slot(str, str, object)
    def dispatch(self, request_id: str, name: str, payload: object) -> None:
        if self.store is None:
            self.finished.emit(request_id, name, {"ok": False, "error": "成品库未就绪"})
            return
        p = dict(payload or {})
        try:
            data = self._execute(name, p)
            self._publish()
            self.finished.emit(request_id, name, {"ok": True, "data": data})
        except Exception as exc:
            self.finished.emit(request_id, name, {"ok": False, "error": str(exc), "code": type(exc).__name__})

    def _execute(self, name: str, p: dict):
        store, submission, library = self.store, self.submission, self.library
        if name == "importAudio":
            return store.import_audio([Path(path) for path in p["paths"]], request_id=p.get("requestId"))
        if name == "setChecked":
            return store.set_checked(p["ids"], bool(p["checked"]))
        if name == "setAllChecked":
            return store.set_all_checked(bool(p["checked"]))
        if name == "removeWork":
            return store.remove_work_items(p["ids"])
        if name == "removeCheckedWork":
            if p.get("scope") == "all":
                return store.remove_checked_work_items()
            if p.get("scope") == "page":
                page = store.numbered_work_page(self._work_page)
                if int(p["queryToken"]) != page["queryToken"]:
                    raise ValueError("待提交列表已变化，请重试")
                ids = p["ids"]
                if not set(ids).issubset({row["taskId"] for row in page["items"]}):
                    raise ValueError("本页范围已变化，请重试")
                return store.remove_checked_work_items(page_ids=ids, query_token=page["queryToken"])
            raise ValueError("无效移除范围")
        if name == "setWorkPage":
            self._work_page = int(p["page"])
            return store.numbered_work_page(self._work_page)
        if name == "reassignInput":
            return store.reassign_input(p["taskId"], Path(p["path"]), int(p["revision"]))
        if name == "setConfig":
            return store.set_config(p["taskId"], p["patch"], int(p["revision"]))
        if name == "setCheckedConfig":
            return store.set_checked_config(p["patch"], int(p["queryToken"]))
        if name == "setOrder":
            store.set_work_item_order(p["ids"])
            return True
        if name == "moveWork":
            store.move_work_item(p["source"], p["target"])
            return True
        if name == "setDevice":
            if p["mode"] not in ("auto", "cpu", "cuda"):
                raise ValueError("无效设备模式")
            if p["mode"] == "cuda":
                resolve_device("cuda")
            store.set_setting("device_mode", p["mode"])
            return p["mode"]
        if name == "prepareSubmission":
            revisions = store.checked_work_revisions()
            return submission.prepare_submission(revisions, device_mode=store.get_setting("device_mode", "auto"))
        if name == "getSubmission":
            session = submission._sessions[p["submissionId"]]
            return {"submissionId": p["submissionId"],
                    "items": [submission._public_item(item) for item in session["items"]]}
        if name == "abandonSubmission":
            return submission.abandon_submission(p["submissionId"])
        if name == "pageWork":
            return store.page_work_items(after=tuple(p["after"]), query_token=int(p["queryToken"]), limit=100)
        if name == "pageHistory":
            return store.list_history(status=self._history_status, query=self._history_query,
                                      after=tuple(p["after"]), query_token=int(p["queryToken"]), limit=100)
        if name == "pageFavorites":
            return store.list_favorites(sort=self._favorite_sort, collection=self._favorite_collection,
                                        query=self._favorite_query, after=tuple(p["after"]),
                                        query_token=int(p["queryToken"]), limit=100)
        if name == "decideConflict":
            return submission.decide_conflict(p["submissionId"], p["taskId"], p["choice"],
                                              bool(p.get("applyToRemaining", False)))
        if name == "previewConflictMidi":
            return submission.preview_conflict_midi(p["submissionId"], p["taskId"])
        if name == "commitSubmission":
            return submission.commit_submission(p["submissionId"])
        if name == "cancelAttempt":
            return submission.cancel_attempt(p["attemptId"])
        if name == "reorderQueued":
            submission.reorder_queued(p["ids"], p["revisions"])
            return True
        if name == "setHistoryFilter":
            self._history_query = p.get("query", "")
            self._history_status = p.get("status", "all")
            return True
        if name == "setFavoriteFilter":
            self._favorite_sort = p.get("sort", "favorited")
            self._favorite_collection = p.get("collection", "all")
            self._favorite_query = p.get("query", "")
            return True
        if name == "addFavorite":
            return store.add_favorite(p["attemptId"])
        if name == "removeFavorites":
            return store.remove_favorites(p["ids"])
        if name == "createCollection":
            return store.create_collection(p["name"])
        if name == "renameCollection":
            return store.rename_collection(p["id"], p["name"], int(p["revision"]))
        if name == "deleteCollection":
            return store.delete_collection(p["id"])
        if name == "setMembership":
            store.set_membership_delta(p["ids"], add=p.get("add", []), remove=p.get("remove", []))
            return True
        if name == "previewDeleteHistory":
            return store.preview_delete_history(p.get("ids"))
        if name == "confirmDeleteHistory":
            return store.confirm_delete_history(p["token"], p["ids"])
        if name == "previewRequeue":
            return library.preview_requeue(p["ids"])
        if name == "requeueEligible":
            return library.requeue_eligible(p["token"], p["ids"])
        if name == "inspectResult":
            return library.inspect_successful_result(p["successRecordId"])
        if name == "beginRecovery":
            return library.begin_result_recovery(p["successRecordId"], int(p["revision"]))
        if name == "createRecoveryWork":
            return library.create_recovery_work_item(p["token"])
        if name == "relocateSuccessInput":
            return library.relocate_success_input(p["successRecordId"], Path(p["path"]), int(p["revision"]))
        if name == "requestPlayback":
            ref = EntryRef(EntryKind(p["kind"]), p["id"])
            return self.files.check(ref, p["mode"])
        if name == "relocate":
            ref = EntryRef(EntryKind(p["kind"]), p["id"])
            return self.files.relocate(ref, p["fileKind"], Path(p["path"]), int(p["revision"]))
        raise ValueError(f"未知命令：{name}")

    @Slot()
    def shutdown(self) -> None:
        if self.timer:
            self.timer.stop()
        if self.submission:
            self.submission.stop_all()
        if self.store:
            self.store.close()
            self.store = None


class WorkbenchFacade(QObject):
    projectionChanged = Signal()
    playbackChanged = Signal()
    commandFinished = Signal(str, str, "QVariant")
    issue = Signal(str)
    pathChosen = Signal(str)
    noteStarted = Signal(int, int)
    noteEnded = Signal(int)
    pedalChanged = Signal(bool)
    notesCleared = Signal()
    _command = Signal(str, str, object)
    _shutdown = Signal()

    def __init__(self, db_path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._projection = {"work": {"items": [], "totalCount": 0, "page": 1,
                                     "pageCount": 1, "pageSize": 20, "checkedCount": 0,
                                     "queryToken": 0}, "active": [],
                            "history": {"items": [], "totalCount": 0},
                            "favorites": {"items": [], "totalCount": 0},
                            "collections": [], "deviceMode": "auto"}
        self._playback = {"state": "idle", "sessionId": None, "durations": {},
                          "positionSeconds": 0, "volumes": {"audio": 1, "midi": .5},
                          "muted": {"audio": False, "midi": False}}
        self._pending_unverified = {}
        soundfont = Path(os.environ.get("PIANOTRANS_SOUNDFONT", str(PROJECT_ROOT / ".codex-cache" / "soundfonts" / "MuseScore_General-0.2.0.sf3")))
        self._player = ProductPlaybackService(None, lambda: (QtAudioBackend(self), _MidiBackend(soundfont, self)))
        self._player.set_event_callback(self._on_playback_event)
        self._player_timer = QTimer(self)
        self._player_timer.timeout.connect(self._sync_playback)
        self._player_timer.start(60)
        self._thread = QThread(self)
        self._worker = _ProductWorker(db_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._command.connect(self._worker.dispatch)
        self._shutdown.connect(self._worker.shutdown, Qt.ConnectionType.BlockingQueuedConnection)
        self._worker.projection.connect(self._on_projection)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self.issue)
        self._thread.start()

    @Property("QVariant", notify=projectionChanged)
    def projection(self):
        return self._projection

    @Property("QVariant", notify=playbackChanged)
    def playback(self):
        return self._playback

    @Slot(str, "QVariant", result=str)
    def command(self, name: str, payload: object = None) -> str:
        request_id = str(uuid4())
        value = payload.toVariant() if isinstance(payload, QJSValue) else payload
        self._command.emit(request_id, name, value or {})
        return request_id

    @Slot(str, "QVariant", result=str)
    def requestPlayback(self, mode: str, entry: object) -> str:
        row = entry.toVariant() if isinstance(entry, QJSValue) else dict(entry)
        self._pending_unverified.clear()
        return self.command("requestPlayback", {"kind": row["kind"], "id": row["id"], "mode": mode})

    @Slot(str)
    def confirmUnverifiedPlayback(self, request_id: str) -> None:
        checked = self._pending_unverified.pop(request_id, None)
        if checked is None:
            self.issue.emit("播放确认已失效")
            return
        if not all(Path(path).is_file() for path in checked["paths"].values()):
            self.issue.emit("原位置的文件已不可读取")
            return
        ref = EntryRef(EntryKind(checked["entryRef"]["kind"]), checked["entryRef"]["id"])
        try:
            self._player.request_verified(ref, checked["mode"], {**checked, "ready": True})
            self._sync_playback()
        except Exception as exc:
            self.issue.emit(str(exc))

    @Slot()
    def pickFiles(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(None, "选择音频", "", "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)")
        if paths:
            self.command("importAudio", {"paths": paths})

    @Slot()
    def pickFolder(self) -> None:
        directory = QFileDialog.getExistingDirectory(None, "选择音频文件夹")
        if directory:
            paths = [str(path) for path in Path(directory).iterdir() if path.is_file() and path.suffix.lower() in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}]
            self.command("importAudio", {"paths": paths})

    @Slot()
    def pickOutputDirectory(self) -> None:
        directory = QFileDialog.getExistingDirectory(None, "选择 MIDI 输出目录")
        if directory:
            self.pathChosen.emit(directory)

    @Slot(str, str, str, int)
    def pickReplacement(self, kind: str, entry_id: str, file_kind: str, revision: int) -> None:
        pattern = "MIDI (*.mid *.midi)" if file_kind == "midi" else "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)"
        path, _ = QFileDialog.getOpenFileName(None, "重新定位文件", "", pattern)
        if path:
            if kind == "task":
                self.command("reassignInput", {"taskId": entry_id, "revision": revision, "path": path})
            else:
                self.command("relocate", {"kind": kind, "id": entry_id, "fileKind": file_kind,
                                          "revision": revision, "path": path})

    @Slot(str, int)
    def pickSuccessInput(self, success_record_id: str, revision: int) -> None:
        path, _ = QFileDialog.getOpenFileName(None, "定位原音频", "", "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)")
        if path:
            self.command("relocateSuccessInput", {"successRecordId": success_record_id,
                                                   "revision": revision, "path": path})

    @Slot(str)
    def openContainingFolder(self, path: str) -> None:
        folder = Path(path).parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot(str)
    def openLegalFile(self, kind: str) -> None:
        filenames = {"license": "LICENSE", "credits": "third_party/byte_dance_core/NOTICE.md",
                     "third_party": "third_party"}
        if kind not in filenames:
            self.issue.emit("未知的许可文件")
            return
        if kind == "third_party":
            candidates = [PROJECT_ROOT.parent / "licenses" / filenames[kind], PROJECT_ROOT / filenames[kind]]
        elif kind == "credits":
            candidates = [PROJECT_ROOT / filenames[kind], PROJECT_ROOT.parent / "licenses" / filenames[kind]]
        else:
            candidates = [PROJECT_ROOT / filenames[kind], PROJECT_ROOT.parent / filenames[kind]]
        for path in candidates:
            if path.is_file() or (kind == "third_party" and path.is_dir()):
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                return
        self.issue.emit("来源或许可文件不存在")

    @Slot(str)
    def copyText(self, value: str) -> None:
        QGuiApplication.clipboard().setText(value)

    @Slot(object)
    def _on_projection(self, value: dict) -> None:
        self._projection = value
        self.projectionChanged.emit()

    @Slot(str, str, object)
    def _on_finished(self, request_id: str, name: str, result: dict) -> None:
        if name == "previewConflictMidi" and result.get("ok"):
            try:
                info = result["data"]
                result = {"ok": True, "data": self._player.request_midi_path(info["path"], info["taskId"])}
                self._sync_playback()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
        if name == "requestPlayback" and result.get("ok") and not result["data"].get("ready"):
            checked = result["data"]
            required = ("audio", "midi") if checked["mode"] == "paired" else (checked["mode"],)
            if all(checked["checks"][kind] in ("match", "unverified") for kind in required) and any(
                    checked["checks"][kind] == "unverified" for kind in required):
                self._pending_unverified[request_id] = checked
                result = {"ok": True, "data": {**checked, "state": "needs_unverified_confirmation"}}
        if name == "requestPlayback" and result.get("ok") and result["data"].get("ready"):
            checked = result["data"]
            ref = EntryRef(EntryKind(checked["entryRef"]["kind"]), checked["entryRef"]["id"])
            try:
                result = {"ok": True, "data": self._player.request_verified(ref, checked["mode"], checked)}
                self._sync_playback()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
        if name == "confirmDeleteHistory" and result.get("ok"):
            source = self._player.snapshot().get("source") or {}
            if source.get("kind") == "history" and source.get("id") in result["data"]["deleted"]:
                self._player.close()
                self._sync_playback()
        self.commandFinished.emit(request_id, name, result)

    def _on_playback_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "note_started":
            self.noteStarted.emit(int(event["note"]), int(event["velocity"]))
        elif kind == "note_ended":
            self.noteEnded.emit(int(event["note"]))
        elif kind == "pedal":
            self.pedalChanged.emit(bool(event["down"]))
        elif kind == "notes_cleared":
            self.notesCleared.emit()
        elif kind == "error":
            self.issue.emit(str(event.get("message", "播放失败")))
        self._sync_playback()

    def _sync_playback(self) -> None:
        snapshot = self._player.snapshot()
        if snapshot != self._playback:
            self._playback = snapshot
            self.playbackChanged.emit()

    @Slot()
    def togglePlayback(self) -> None:
        try:
            snapshot = self._player.snapshot()
            if snapshot["state"] == "playing":
                self._player.pause(snapshot["sessionId"])
            else:
                self._player.play(snapshot["sessionId"])
            self._sync_playback()
        except Exception as exc:
            self.issue.emit(str(exc))

    @Slot()
    def stopPlayback(self) -> None:
        try:
            self._player.stop(self._player.snapshot()["sessionId"])
            self._sync_playback()
        except Exception as exc:
            self.issue.emit(str(exc))

    @Slot(float)
    def seekPlayback(self, seconds: float) -> None:
        try:
            self._player.seek(self._player.snapshot()["sessionId"], seconds)
            self._sync_playback()
        except Exception as exc:
            self.issue.emit(str(exc))

    @Slot(str, float)
    def setVolume(self, track: str, value: float) -> None:
        self._player.set_volume(track, value)
        self._sync_playback()

    @Slot(str, bool)
    def setMuted(self, track: str, muted: bool) -> None:
        self._player.set_muted(track, muted)
        self._sync_playback()

    def close(self) -> None:
        self._player_timer.stop()
        self._player.close()
        if self._thread.isRunning():
            self._shutdown.emit()
            self._thread.quit()
            self._thread.wait(5000)
