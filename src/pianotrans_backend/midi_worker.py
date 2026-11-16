"""Isolated FluidSynth MIDI worker and its host-side client."""

from __future__ import annotations

import os
import queue
import threading
import time
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Callable

from .midi_player import MidiTimeline

_DLL_HANDLES = []

def _configure_dll_path() -> None:
    configured = os.environ.get("FLUIDSYNTH_BIN_DIR")
    candidates = [Path(configured)] if configured else []
    cache = Path(__file__).resolve().parents[2] / ".codex-cache"
    candidates.extend(cache.glob("fluidsynth-*/fluidsynth-*/bin"))
    for candidate in candidates:
        if candidate.is_dir():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                _DLL_HANDLES.append(os.add_dll_directory(str(candidate)))
            return


def _load_fluidsynth():
    _configure_dll_path()
    try:
        import fluidsynth
    except ImportError:
        return None
    return fluidsynth


def run_midi_worker(connection: Connection, temp_dir: str) -> None:
    """Process entry point. Only structured events cross the pipe."""
    fluidsynth = _load_fluidsynth()
    set_gain = None
    get_current_tick = None
    if fluidsynth is not None:
        try:
            set_gain = fluidsynth.cfunc(
                "fluid_synth_set_gain",
                fluidsynth.c_int,
                ("synth", fluidsynth.c_void_p, 1),
                ("gain", fluidsynth.c_float, 1),
            )
        except Exception:
            set_gain = None
        get_current_tick = getattr(fluidsynth, "fluid_player_get_current_tick", None)
        if get_current_tick is None:
            get_current_tick = fluidsynth.cfunc(
                "fluid_player_get_current_tick",
                fluidsynth.c_int,
                ("player", fluidsynth.c_void_p, 1),
            )
    synth = None
    player = None
    timeline: MidiTimeline | None = None
    midi_path = ""
    soundfont_path = ""
    volume = 0.5
    paused_tick = 0
    current_tick = 0
    base_tick = 0
    events: queue.Queue[dict[str, Any]] = queue.Queue()
    callback = None

    def send(event: dict[str, Any]) -> None:
        try:
            connection.send(event)
        except (BrokenPipeError, EOFError, OSError):
            pass

    def release_player() -> None:
        nonlocal player
        if player is None or fluidsynth is None:
            return
        try:
            fluidsynth.fluid_player_stop(player)
        except Exception:
            pass
        try:
            fluidsynth.delete_fluid_player(player)
        except Exception:
            pass
        player = None

    def release_synth() -> None:
        nonlocal synth
        release_player()
        if synth is not None:
            try:
                synth.delete()
            except Exception:
                pass
            synth = None

    def make_callback():
        callback_type = fluidsynth.CFUNCTYPE(fluidsynth.c_int, fluidsynth.c_void_p, fluidsynth.c_void_p)

        def handle(data, event):
            result = fluidsynth.fluid_synth_handle_midi_event(data, event)
            event_type = int(fluidsynth.fluid_midi_event_get_type(event)) & 0xF0
            note = int(fluidsynth.fluid_midi_event_get_key(event))
            velocity = int(fluidsynth.fluid_midi_event_get_velocity(event))
            if event_type == 0x90 and velocity > 0:
                events.put({"type": "note_started", "note": note, "velocity": velocity})
            elif event_type in {0x80, 0x90}:
                events.put({"type": "note_ended", "note": note})
            elif event_type == 0xB0 and int(fluidsynth.fluid_midi_event_get_control(event)) == 64:
                events.put({"type": "pedal", "down": int(fluidsynth.fluid_midi_event_get_value(event)) >= 64})
            return result

        return callback_type(handle)

    def prepare(payload: dict[str, Any]) -> None:
        nonlocal synth, timeline, midi_path, soundfont_path, callback, current_tick, paused_tick
        if fluidsynth is None:
            send({"type": "error", "code": "fluidsynth_unavailable", "message": "FluidSynth 不可用"})
            return
        try:
            release_synth()
            midi_path = str(payload["midi_path"])
            soundfont_path = str(payload["soundfont_path"])
            timeline = MidiTimeline.read(midi_path)
            synth = fluidsynth.Synth(gain=volume)
            synth.audio_driver = fluidsynth.new_fluid_audio_driver(synth.settings, synth.synth)
            if not synth.audio_driver:
                raise RuntimeError("无法打开音频输出设备")
            sfid = synth.sfload(soundfont_path)
            if sfid < 0:
                raise RuntimeError("无法加载 SoundFont")
            synth.program_select(0, sfid, 0, 0)
            callback = make_callback()
            synth.custom_router_callback = callback
            current_tick = paused_tick = 0
            send({"type": "ready", "duration": timeline.duration})
        except Exception as exc:
            release_synth()
            send({"type": "error", "code": "midi_prepare_failed", "message": str(exc)})

    def start() -> None:
        nonlocal player, current_tick, paused_tick, base_tick
        if synth is None or timeline is None:
            send({"type": "error", "code": "midi_not_prepared", "message": "MIDI 尚未准备"})
            return
        try:
            release_player()
            play_path = midi_path
            if paused_tick > 0:
                play_path = str(timeline.create_seek_file(paused_tick, Path(temp_dir)))
            status = synth.play_midi_file(play_path)
            player = getattr(synth, "player", None)
            if status < 0 or player is None:
                raise RuntimeError("FluidSynth 原生 Player 无法播放 MIDI")
            current_tick = paused_tick
            base_tick = paused_tick
            paused_tick = 0
            send({"type": "playing"})
        except Exception as exc:
            send({"type": "error", "code": "midi_start_failed", "message": str(exc)})

    running = True
    try:
        while running:
            while True:
                try:
                    send(events.get_nowait())
                except queue.Empty:
                    break
            if player is not None and fluidsynth is not None and timeline is not None:
                try:
                    status = fluidsynth.fluid_player_get_status(player)
                    if get_current_tick is not None:
                        current_tick = min(timeline.total_ticks, max(0, base_tick + int(get_current_tick(player))))
                    send({"type": "position", "seconds": timeline.tick_to_seconds(current_tick)})
                    if status in {fluidsynth.FLUID_PLAYER_DONE, fluidsynth.FLUID_PLAYER_STOPPING}:
                        release_player()
                        if status == fluidsynth.FLUID_PLAYER_DONE:
                            current_tick = timeline.total_ticks
                            send({"type": "position", "seconds": timeline.duration})
                            send({"type": "ended"})
                        else:
                            send({"type": "stopped"})
                except Exception as exc:
                    send({"type": "error", "code": "midi_runtime_failed", "message": str(exc)})
                    release_player()
            if connection.poll(0.03):
                try:
                    payload = connection.recv()
                except (EOFError, OSError):
                    break
                command = payload.get("type")
                if command == "prepare":
                    prepare(payload)
                elif command == "start":
                    start()
                elif command == "pause":
                    if player is not None:
                        paused_tick = current_tick
                        release_player()
                    send({"type": "paused"})
                elif command == "stop":
                    release_player()
                    paused_tick = 0
                    send({"type": "stopped", "position": 0.0})
                    send({"type": "notes_cleared"})
                elif command == "seek" and timeline is not None:
                    paused_tick = timeline.seconds_to_tick(float(payload.get("seconds", 0.0)))
                    current_tick = paused_tick
                    release_player()
                    send({"type": "position", "seconds": timeline.tick_to_seconds(paused_tick)})
                    send({"type": "ready", "duration": timeline.duration})
                elif command == "set_volume":
                    volume = min(1.0, max(0.0, float(payload.get("value", volume))))
                    if synth is not None:
                        if set_gain is not None:
                            set_gain(synth.synth, volume)
                elif command == "shutdown":
                    running = False
            time.sleep(0.01)
    finally:
        send({"type": "stopped"})
        release_synth()
        try:
            connection.close()
        except OSError:
            pass


class MidiWorkerClient:
    """Thread-safe host-side client for the isolated MIDI worker."""

    def __init__(self, soundfont_path: str | Path, temp_dir: str | Path | None = None) -> None:
        self.soundfont_path = str(soundfont_path)
        default_temp = Path(os.environ.get("PIANOTRANS_TEMP_DIR", str(Path(__file__).resolve().parents[2] / ".codex-tmp")))
        self.temp_dir = Path(temp_dir or default_temp)
        self._process = None
        self._connection: Connection | None = None
        self._reader: threading.Thread | None = None
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self._lock = threading.Lock()

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._callback = callback

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        context = get_context("spawn")
        host, child = context.Pipe()
        self._connection = host
        self._process = context.Process(target=run_midi_worker, args=(child, str(self.temp_dir)), daemon=True)
        self._process.start()
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()

    def _send(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_process()
            if self._connection is None:
                raise RuntimeError("MIDI worker 未启动")
            self._connection.send(payload)

    def _read_events(self) -> None:
        connection = self._connection
        while connection is not None:
            try:
                event = connection.recv()
            except (EOFError, OSError):
                return
            if self._callback is not None:
                self._callback(event)

    def prepare(self, path: str) -> bool:
        self._send({"type": "prepare", "midi_path": path, "soundfont_path": self.soundfont_path})
        return False

    def start(self) -> None:
        self._send({"type": "start"})

    def pause(self) -> None:
        self._send({"type": "pause"})

    def stop(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._send({"type": "stop"})

    def seek(self, seconds: float) -> None:
        self._send({"type": "seek", "seconds": seconds})

    def set_volume(self, value: float) -> None:
        self._send({"type": "set_volume", "value": value})

    def close(self, force: bool = False) -> None:
        process = self._process
        if process is None:
            return
        if process.is_alive():
            try:
                self._send({"type": "shutdown"})
            except (EOFError, OSError):
                pass
            process.join(timeout=0.5)
        if force and process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        self._process = None
        self._connection = None
