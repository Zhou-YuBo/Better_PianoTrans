"""Qt adapters and signal bridge for the production playback coordinator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from pianotrans_backend.midi_worker import MidiWorkerClient
from pianotrans_backend.playback import PlaybackCoordinator, PlaybackState

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover - exercised only on incomplete installs
    QAudioOutput = None
    QMediaPlayer = None


class QtAudioBackend(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self._player = QMediaPlayer(self) if QMediaPlayer is not None else None
        self._output = QAudioOutput(self) if QAudioOutput is not None else None
        self._path = ""
        if self._player is not None and self._output is not None:
            self._player.setAudioOutput(self._output)
            self._player.durationChanged.connect(lambda value: self._emit({"type": "duration", "value": value / 1000.0}))
            self._player.positionChanged.connect(lambda value: self._emit({"type": "position", "seconds": value / 1000.0}))
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._player.errorOccurred.connect(lambda _error, message: self._emit({"type": "error", "code": "audio_playback_failed", "message": message or "无法播放音频"}))

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._callback = callback

    def prepare(self, path: str) -> bool:
        if self._player is None or self._output is None:
            self._emit({"type": "error", "code": "qt_multimedia_unavailable", "message": "Qt Multimedia 不可用"})
            return False
        file_path = Path(path)
        if not file_path.is_file():
            self._emit({"type": "error", "code": "audio_missing", "message": "音频文件不存在"})
            return False
        self._path = str(file_path.resolve())
        self._player.setSource(QUrl.fromLocalFile(self._path))
        return False

    def start(self) -> None:
        if self._player is None:
            raise RuntimeError("Qt Multimedia 不可用")
        self._player.play()

    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def seek(self, seconds: float) -> None:
        if self._player is not None:
            self._player.setPosition(max(0, int(seconds * 1000)))

    def set_volume(self, value: float) -> None:
        if self._output is not None:
            self._output.setVolume(min(1.0, max(0.0, float(value))))

    def close(self) -> None:
        self.stop()
        if self._player is not None:
            self._player.setSource(QUrl())

    def _emit(self, event: dict[str, Any]) -> None:
        if self._callback is not None:
            self._callback(event)

    def _on_state(self, state) -> None:
        if self._player is None:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._emit({"type": "playing"})
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._emit({"type": "paused"})
        else:
            self._emit({"type": "stopped"})

    def _on_media_status(self, status) -> None:
        if QMediaPlayer is not None and status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            self._emit({"type": "ready"})
        elif QMediaPlayer is not None and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._emit({"type": "ended"})


class AudioPlayerBridge(QtAudioBackend):
    fileChanged = Signal()
    durationChanged = Signal()
    positionChanged = Signal()
    playingChanged = Signal()
    statusChanged = Signal()
    volumeChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._duration = 0
        self._position = 0
        self._playing = False
        self._status = ""
        self._volume = 1.0
        self.set_event_callback(self._on_event)

    @Property(str, notify=fileChanged)
    def fileName(self) -> str:
        return Path(self._path).name if self._path else ""

    @Property(str, notify=fileChanged)
    def filePath(self) -> str:
        return self._path

    @Property(int, notify=durationChanged)
    def duration(self) -> int:
        return self._duration

    @Property(int, notify=positionChanged)
    def position(self) -> int:
        return self._position

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(float, notify=volumeChanged)
    def volume(self) -> float:
        return self._volume

    @Slot()
    def chooseFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(None, "选择音频", str(Path.home()), "音频 (*.wav *.mp3 *.flac *.m4a *.ogg *.aac)")
        if path:
            self.loadFile(path)

    @Slot(str)
    def loadFile(self, path: str) -> None:
        if self.prepare(path):
            self.fileChanged.emit()
        elif self._path:
            self.fileChanged.emit()

    @Slot()
    def play(self) -> None:
        try:
            self.start()
        except Exception as exc:
            self._set_status(str(exc))

    @Slot()
    def pause(self) -> None:
        super().pause()

    @Slot(int)
    def seek(self, position: int) -> None:
        super().seek(position / 1000.0)

    @Slot(float)
    def setVolume(self, value: float) -> None:
        self._volume = min(1.0, max(0.0, float(value)))
        super().set_volume(self._volume)
        self.volumeChanged.emit()

    def _on_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "duration":
            self._duration = int(float(event.get("value", 0)) * 1000)
            self.durationChanged.emit()
        elif event_type == "position":
            self._position = int(float(event.get("seconds", 0)) * 1000)
            self.positionChanged.emit()
        elif event_type == "playing":
            self._playing = True
            self.playingChanged.emit()
        elif event_type == "stopped":
            self._playing = False
            self.playingChanged.emit()
        elif event_type == "error":
            self._set_status(str(event.get("message", "无法播放音频")))
        elif event_type == "ready":
            self._set_status("已载入")

    def _set_status(self, value: str) -> None:
        if value != self._status:
            self._status = value
            self.statusChanged.emit()


class MidiPlayerBridge(QObject):
    midiChanged = Signal()
    soundFontChanged = Signal()
    playingChanged = Signal()
    statusChanged = Signal()
    volumeChanged = Signal()
    progressChanged = Signal()
    positionChanged = Signal()
    durationChanged = Signal()
    noteStarted = Signal(int, int)
    noteEnded = Signal(int)
    pedalChanged = Signal(bool)
    notesCleared = Signal()
    preparedChanged = Signal()

    def __init__(self, soundfont_path: str | Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        default = soundfont_path or (Path(__file__).resolve().parents[2] / ".codex-cache" / "soundfonts" / "MuseScore_General-0.2.0.sf3")
        self._midi_path = ""
        self._soundfont_path = str(default)
        self._volume = 0.5
        self._playing = False
        self._prepared = False
        self._duration = 0.0
        self._position = 0.0
        self._status = ""
        self._client = MidiWorkerClient(self._soundfont_path)
        self._client.set_event_callback(self._on_event)

    @Property(str, notify=midiChanged)
    def midiFileName(self) -> str:
        return Path(self._midi_path).name if self._midi_path else ""

    @Property(str, notify=soundFontChanged)
    def soundFontFileName(self) -> str:
        return Path(self._soundfont_path).name if self._soundfont_path else ""

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(float, notify=volumeChanged)
    def volume(self) -> float:
        return self._volume

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return min(1.0, max(0.0, self._position / self._duration)) if self._duration else 0.0

    @Property(float, notify=positionChanged)
    def position(self) -> float:
        return self._position

    @Property(float, notify=durationChanged)
    def duration(self) -> float:
        return self._duration

    @Property(bool, notify=preparedChanged)
    def prepared(self) -> bool:
        return self._prepared

    @Property(str, notify=positionChanged)
    def positionText(self) -> str:
        return self._format_time(self._position)

    @Property(str, notify=durationChanged)
    def durationText(self) -> str:
        return self._format_time(self._duration)

    @Slot()
    def chooseMidi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(None, "选择 MIDI", str(Path.home()), "MIDI (*.mid *.midi)")
        if path:
            self._midi_path = str(Path(path).resolve())
            self.midiChanged.emit()

    @Slot()
    def chooseSoundFont(self) -> None:
        path, _ = QFileDialog.getOpenFileName(None, "选择 SoundFont", str(Path.home()), "SoundFont (*.sf2 *.sf3)")
        if path:
            self._soundfont_path = str(Path(path).resolve())
            self._client.soundfont_path = self._soundfont_path
            self.soundFontChanged.emit()

    @Slot()
    def prepare(self) -> None:
        if not self._midi_path:
            self._set_status("请先选择 MIDI")
            return
        self._client.prepare(self._midi_path)
        self._set_status("正在准备 SoundFont")

    @Slot()
    def play(self) -> None:
        if not self._prepared:
            self.prepare()
            return
        self._client.start()

    @Slot()
    def pause(self) -> None:
        self._client.pause()

    @Slot()
    def stop(self) -> None:
        self._client.stop()

    @Slot()
    def releasePrepared(self) -> None:
        self._client.close(force=True)
        self._prepared = False
        self.preparedChanged.emit()

    @Slot(float)
    def seekProgress(self, progress: float) -> None:
        self._client.seek(max(0.0, min(1.0, progress)) * self._duration)

    @Slot(float)
    def setVolume(self, value: float) -> None:
        self._volume = min(1.0, max(0.0, float(value)))
        self._client.set_volume(self._volume)
        self.volumeChanged.emit()

    def _on_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "ready":
            self._duration = float(event.get("duration", 0.0))
            self._prepared = True
            self.durationChanged.emit(); self.preparedChanged.emit()
            self._set_status("已准备 SoundFont")
        elif event_type == "playing":
            self._playing = True; self.playingChanged.emit()
        elif event_type == "paused":
            self._playing = False; self.playingChanged.emit()
        elif event_type in {"stopped", "ended"}:
            self._playing = False; self.playingChanged.emit()
            if event_type == "ended": self._position = self._duration; self.positionChanged.emit()
        elif event_type == "position":
            self._position = float(event.get("seconds", 0.0)); self.positionChanged.emit(); self.progressChanged.emit()
        elif event_type == "note_started": self.noteStarted.emit(int(event["note"]), int(event["velocity"]))
        elif event_type == "note_ended": self.noteEnded.emit(int(event["note"]))
        elif event_type == "pedal": self.pedalChanged.emit(bool(event["down"]))
        elif event_type == "notes_cleared": self.notesCleared.emit()
        elif event_type == "error": self._set_status(str(event.get("message", "MIDI 播放失败")))

    def _set_status(self, value: str) -> None:
        if value != self._status:
            self._status = value; self.statusChanged.emit()

    @staticmethod
    def _format_time(value: float) -> str:
        total = max(0, int(value)); return f"{total // 60}:{total % 60:02d}"


class PlaybackService(QObject):
    stateChanged = Signal(str)
    positionChanged = Signal(float)
    durationChanged = Signal(float)
    error = Signal(str, str)
    verificationWarning = Signal(str, object)
    noteStarted = Signal(int, int)
    noteEnded = Signal(int)
    pedalChanged = Signal(bool)
    notesCleared = Signal()

    def __init__(self, repository, soundfont_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.audio = QtAudioBackend(self)
        self.midi = MidiWorkerClient(soundfont_path)
        self.coordinator = PlaybackCoordinator(repository, self.audio, self.midi)
        self._autostart = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(30)

    @property
    def state(self) -> PlaybackState:
        return self.coordinator.state

    def prepare_history(self, history_id: str, allow_unverified: bool = False) -> None:
        self._autostart = True
        self.coordinator.prepare_history(history_id, allow_unverified)
        self._notify_state()

    def preview_audio(self, path: str) -> None:
        self._autostart = False
        self.coordinator.prepare_audio(path)
        self._notify_state()

    def preview_midi(self, path: str) -> None:
        self._autostart = False
        self.coordinator.prepare_midi(path)
        self._notify_state()

    def start(self) -> None:
        self._autostart = False
        self.coordinator.start()
        self._notify_state()

    def pause(self) -> None:
        self.coordinator.pause()
        self._notify_state()

    def stop(self) -> None:
        self._autostart = False
        self.coordinator.stop()
        self._notify_state()

    def seek(self, seconds: float) -> None:
        self.coordinator.seek(seconds)
        self._notify_state()

    def set_audio_volume(self, value: float) -> None:
        self.coordinator.set_audio_volume(value)

    def set_midi_volume(self, value: float) -> None:
        self.coordinator.set_midi_volume(value)

    def close(self) -> None:
        self._timer.stop()
        self.coordinator.close()

    def _drain_events(self) -> None:
        for event in self.coordinator.poll_events():
            event_type = event.get("type")
            if event_type == "position":
                self.positionChanged.emit(float(event.get("seconds", 0.0)))
            elif event_type == "duration":
                self.durationChanged.emit(float(event.get("value", 0.0)))
            elif event_type == "ready" and event.get("source") == "midi":
                self.durationChanged.emit(float(event.get("duration", 0.0)))
            elif event_type == "error":
                self.error.emit(str(event.get("code", "playback_failed")), str(event.get("message", "播放失败")))
            elif event_type == "note_started":
                self.noteStarted.emit(int(event.get("note", 0)), int(event.get("velocity", 0)))
            elif event_type == "note_ended":
                self.noteEnded.emit(int(event.get("note", 0)))
            elif event_type == "pedal":
                self.pedalChanged.emit(bool(event.get("down", False)))
            elif event_type == "notes_cleared":
                self.notesCleared.emit()
        if self._autostart and self.coordinator.state == PlaybackState.READY:
            self._autostart = False
            try:
                self.coordinator.start()
            except Exception as exc:
                self.error.emit("playback_start_failed", str(exc))
        self._notify_state()

    def _notify_state(self) -> None:
        self.stateChanged.emit(self.coordinator.state.value)
