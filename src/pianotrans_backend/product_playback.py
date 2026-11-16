"""Independent playback sessions for task, history, and favorite entries."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from .product_contract import EntryRef
from .product_files import ProductFileService


class PlaybackSessionError(ValueError):
    pass


class ProductPlaybackService:
    def __init__(self, files: ProductFileService | None, backend_factory: Callable[[], tuple]) -> None:
        self.files = files
        self._backend_factory = backend_factory
        self._backends: dict = {}
        self._ready: dict[str, bool] = {}
        self._session_id: str | None = None
        self._source: EntryRef | dict | None = None
        self._mode: str | None = None
        self._state = "idle"
        self._position = 0.0
        self._durations = {"audio": 0.0, "midi": 0.0}
        self._volumes = {"audio": 1.0, "midi": 0.5}
        self._muted = {"audio": False, "midi": False}
        self._on_event: Callable[[dict], None] | None = None

    def set_event_callback(self, callback: Callable[[dict], None]) -> None:
        self._on_event = callback

    def snapshot(self) -> dict:
        source = ({"kind": self._source.kind.value, "id": self._source.id}
                  if isinstance(self._source, EntryRef) else self._source)
        return {"sessionId": self._session_id, "source": source,
                "mode": self._mode, "state": self._state, "positionSeconds": self._position,
                "durations": dict(self._durations), "volumes": dict(self._volumes), "muted": dict(self._muted)}

    def request(self, ref: EntryRef, mode: str) -> dict:
        if self.files is None:
            raise PlaybackSessionError("file checks must be provided by the service thread")
        checked = self.files.check(ref, mode)
        return self.request_verified(ref, mode, checked)

    def request_verified(self, ref: EntryRef, mode: str, checked: dict) -> dict:
        if not checked["ready"]:
            return {"state": "needs_file_resolution", **checked}
        return self._open_verified(ref, mode, checked)

    def request_midi_path(self, path: str, task_id: str) -> dict:
        return self._open_verified({"kind": "conflict", "id": task_id}, "midi",
                                   {"paths": {"midi": path}})

    def _open_verified(self, source: EntryRef | dict, mode: str, checked: dict) -> dict:
        self._release_backends()
        session_id = str(uuid4())
        self._session_id = session_id
        self._source = source
        self._mode = mode
        self._state = "preparing"
        self._position = 0.0
        self._durations = {"audio": 0.0, "midi": 0.0}
        audio, midi = self._backend_factory()
        self._backends = {"audio": audio, "midi": midi}
        kinds = ("audio", "midi") if mode == "paired" else (mode,)
        self._ready = {kind: False for kind in kinds}
        for kind in kinds:
            backend = self._backends[kind]
            backend.set_event_callback(lambda event, name=kind, sid=session_id: self._backend_event(sid, name, event))
            self._ready[kind] = bool(backend.prepare(checked["paths"][kind])) or self._ready[kind]
            backend.set_volume(0.0 if self._muted[kind] else self._volumes[kind])
        self._start_if_ready()
        return self.snapshot()

    def _backend_event(self, session_id: str, kind: str, event: dict) -> None:
        if session_id != self._session_id:
            return
        event_type = event.get("type")
        if event_type == "ready":
            if "duration" in event:
                self._durations[kind] = float(event["duration"])
            self._ready[kind] = True
            self._start_if_ready()
        elif event_type == "duration":
            self._durations[kind] = float(event.get("value", event.get("duration", 0)))
        elif event_type == "position":
            self._position = float(event.get("seconds", event.get("position", 0)))
        elif event_type == "ended" and self._state == "playing" and self._mode != "paired":
            self._backends[kind].seek(0.0)
            self._backends[kind].start()
            self._position = 0.0
        elif event_type == "error":
            self._state = "error"
        if self._on_event is not None:
            self._on_event({"sessionId": session_id, "track": kind, **event})

    def _start_if_ready(self) -> None:
        if self._state == "preparing" and self._ready and all(self._ready.values()):
            self._state = "playing"
            for kind in self._ready:
                self._backends[kind].start()

    def _require(self, session_id: str) -> None:
        if session_id != self._session_id or not session_id:
            raise PlaybackSessionError("playback session changed")

    def play(self, session_id: str) -> None:
        self._require(session_id)
        if self._state in ("paused", "stopped", "ready"):
            for kind in self._ready:
                self._backends[kind].start()
            self._state = "playing"

    def pause(self, session_id: str) -> None:
        self._require(session_id)
        if self._state == "playing":
            for kind in self._ready:
                self._backends[kind].pause()
            self._state = "paused"

    def stop(self, session_id: str) -> None:
        self._require(session_id)
        for kind in self._ready:
            self._backends[kind].stop()
            self._backends[kind].seek(0.0)
        self._position = 0.0
        self._state = "stopped"

    def seek(self, session_id: str, seconds: float) -> None:
        self._require(session_id)
        position = max(0.0, float(seconds))
        if "midi" in self._ready:
            self._clear_notes()
        for kind in self._ready:
            self._backends[kind].seek(position)
        self._position = position
        if self._state == "stopped":
            self.play(session_id)
        elif self._state == "playing" and "midi" in self._ready:
            self._backends["midi"].start()

    def set_volume(self, track: str, value: float) -> None:
        if track not in self._volumes:
            raise ValueError("unknown playback track")
        self._volumes[track] = min(1.0, max(0.0, float(value)))
        self._muted[track] = False
        if track in self._backends:
            self._backends[track].set_volume(self._volumes[track])

    def set_muted(self, track: str, muted: bool) -> None:
        if track not in self._muted:
            raise ValueError("unknown playback track")
        self._muted[track] = bool(muted)
        if track in self._backends:
            self._backends[track].set_volume(0.0 if muted else self._volumes[track])

    def _release_backends(self) -> None:
        if "midi" in self._ready:
            self._clear_notes()
        for backend in self._backends.values():
            backend.stop()
            backend.close()
            if callable(getattr(backend, "deleteLater", None)):
                backend.deleteLater()
        self._backends = {}
        self._ready = {}

    def _clear_notes(self) -> None:
        if self._on_event is not None:
            self._on_event({"type": "notes_cleared", "sessionId": self._session_id, "track": "midi"})

    def close(self) -> None:
        self._release_backends()
        self._session_id = None
        self._source = None
        self._mode = None
        self._state = "idle"
