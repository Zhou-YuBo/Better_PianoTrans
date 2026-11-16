"""History-backed coordination for audio and MIDI playback."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .artifacts import ArtifactVerification, VerificationResult, verify_file
from .history import HistoryAttempt, HistoryRepository


class PlaybackState(StrEnum):
    IDLE = "idle"
    VERIFYING = "verifying"
    PREPARING = "preparing"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class PlaybackMode(StrEnum):
    PAIRED = "paired"
    AUDIO_ONLY = "audio_only"
    MIDI_ONLY = "midi_only"


class PlaybackError(RuntimeError):
    def __init__(self, message: str, verification: tuple[VerificationResult, VerificationResult] = ()) -> None:
        super().__init__(message)
        self.verification = verification


class AudioBackend(Protocol):
    def prepare(self, path: str) -> bool: ...
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def set_volume(self, value: float) -> None: ...


class MidiBackend(Protocol):
    def prepare(self, path: str) -> bool: ...
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def set_volume(self, value: float) -> None: ...


@dataclass(frozen=True)
class PlaybackPreparation:
    history: HistoryAttempt | None
    input_verification: VerificationResult | None
    output_verification: VerificationResult | None
    mode: PlaybackMode = PlaybackMode.PAIRED


class PlaybackCoordinator:
    """Coordinates verification, readiness, and lifecycle of playback backends."""

    def __init__(self, repository: HistoryRepository, audio: AudioBackend, midi: MidiBackend) -> None:
        self._repository = repository
        self._audio = audio
        self._midi = midi
        self._state = PlaybackState.IDLE
        self._mode: PlaybackMode | None = None
        self._preparation: PlaybackPreparation | None = None
        self._audio_ready = False
        self._midi_ready = False
        self._events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        for backend, kind in ((audio, "audio"), (midi, "midi")):
            setter = getattr(backend, "set_event_callback", None)
            if setter is not None:
                setter(lambda event, source=kind: self._events.put({"source": source, **event}))

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def mode(self) -> PlaybackMode | None:
        return self._mode

    @property
    def preparation(self) -> PlaybackPreparation | None:
        return self._preparation

    def prepare_history(self, history_id: str, allow_unverified: bool = False) -> PlaybackPreparation:
        self.stop()
        self._state = PlaybackState.VERIFYING
        history = self._repository.get_attempt(history_id)
        if history is None:
            raise PlaybackError("历史记录不存在")
        input_result = verify_file(Path(history.input_path), history.input_sha256, history.input_size_bytes, history.input_mtime_ns)
        output_result = verify_file(Path(history.midi_path), history.output_sha256, history.output_size_bytes, history.output_mtime_ns)
        report = (input_result, output_result)
        warning = {ArtifactVerification.MISMATCH, ArtifactVerification.UNVERIFIED}
        if (input_result.status in warning or output_result.status in warning) and not allow_unverified:
            self._state = PlaybackState.ERROR
            raise PlaybackError("播放文件指纹不一致，需要确认", report)
        if input_result.status == ArtifactVerification.UNREADABLE or output_result.status == ArtifactVerification.UNREADABLE:
            self._state = PlaybackState.ERROR
            raise PlaybackError("播放文件不可用", report)
        if input_result.status == ArtifactVerification.MISSING and output_result.status == ArtifactVerification.MISSING:
            self._state = PlaybackState.ERROR
            raise PlaybackError("播放文件不可用", report)
        mode = PlaybackMode.PAIRED
        if input_result.status == ArtifactVerification.MISSING:
            mode = PlaybackMode.MIDI_ONLY
        elif output_result.status == ArtifactVerification.MISSING:
            mode = PlaybackMode.AUDIO_ONLY
        self._preparation = PlaybackPreparation(history, input_result, output_result, mode)
        self._mode = mode
        self._state = PlaybackState.PREPARING
        self._audio_ready = mode == PlaybackMode.MIDI_ONLY
        self._midi_ready = mode == PlaybackMode.AUDIO_ONLY
        if mode != PlaybackMode.MIDI_ONLY:
            self._audio_ready = bool(self._audio.prepare(history.input_path))
        if mode != PlaybackMode.AUDIO_ONLY:
            self._midi_ready = bool(self._midi.prepare(history.midi_path))
        self._mark_ready()
        return self._preparation

    def prepare_audio(self, path: str) -> None:
        self.stop()
        self._mode = PlaybackMode.AUDIO_ONLY
        self._preparation = PlaybackPreparation(None, None, None, self._mode)
        self._state = PlaybackState.PREPARING
        self._audio_ready = bool(self._audio.prepare(path))
        self._midi_ready = True
        self._mark_ready()

    def prepare_midi(self, path: str) -> None:
        self.stop()
        self._mode = PlaybackMode.MIDI_ONLY
        self._preparation = PlaybackPreparation(None, None, None, self._mode)
        self._state = PlaybackState.PREPARING
        self._audio_ready = True
        self._midi_ready = bool(self._midi.prepare(path))
        self._mark_ready()

    def mark_audio_ready(self) -> None:
        self._audio_ready = True
        self._mark_ready()

    def mark_midi_ready(self) -> None:
        self._midi_ready = True
        self._mark_ready()

    def start(self) -> None:
        if self._state != PlaybackState.READY:
            raise PlaybackError("播放后端尚未准备完成")
        try:
            if self._mode != PlaybackMode.MIDI_ONLY:
                self._audio.start()
            if self._mode != PlaybackMode.AUDIO_ONLY:
                self._midi.start()
        except Exception:
            self._stop_backends()
            self._state = PlaybackState.ERROR
            raise
        self._state = PlaybackState.PLAYING

    def pause(self) -> None:
        if self._state != PlaybackState.PLAYING:
            return
        if self._mode != PlaybackMode.MIDI_ONLY:
            self._audio.pause()
        if self._mode != PlaybackMode.AUDIO_ONLY:
            self._midi.pause()
        self._state = PlaybackState.PAUSED

    def seek(self, seconds: float) -> None:
        if self._preparation is None:
            raise PlaybackError("尚未载入播放对象")
        was_playing = self._state == PlaybackState.PLAYING
        self._stop_backends()
        if self._mode != PlaybackMode.MIDI_ONLY:
            self._audio.seek(max(0.0, float(seconds)))
        if self._mode != PlaybackMode.AUDIO_ONLY:
            self._midi.seek(max(0.0, float(seconds)))
        self._state = PlaybackState.READY
        if was_playing:
            self.start()

    def set_audio_volume(self, value: float) -> None:
        self._audio.set_volume(min(1.0, max(0.0, float(value))))

    def set_midi_volume(self, value: float) -> None:
        self._midi.set_volume(min(1.0, max(0.0, float(value))))

    def stop(self) -> None:
        self._stop_backends()
        if self._state != PlaybackState.IDLE:
            self._state = PlaybackState.STOPPED
        self._audio_ready = False
        self._midi_ready = False

    def close(self) -> None:
        self.stop()
        closer = getattr(self._midi, "close", None)
        if closer is not None:
            closer(force=True)
        closer = getattr(self._audio, "close", None)
        if closer is not None:
            closer()

    def poll_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        for event in events:
            source = event.get("source")
            event_type = event.get("type")
            if source == "audio" and event_type == "ready":
                self.mark_audio_ready()
            elif source == "midi" and event_type == "ready":
                self.mark_midi_ready()
            elif event_type == "error":
                self._state = PlaybackState.ERROR
            elif event_type == "ended" and self._state == PlaybackState.PLAYING:
                self._state = PlaybackState.STOPPED
        return events

    def _stop_backends(self) -> None:
        try:
            if self._mode != PlaybackMode.MIDI_ONLY:
                self._audio.stop()
            if self._mode != PlaybackMode.AUDIO_ONLY:
                self._midi.stop()
        except Exception:
            pass

    def _mark_ready(self) -> None:
        if self._state == PlaybackState.PREPARING and self._audio_ready and self._midi_ready:
            self._state = PlaybackState.READY
