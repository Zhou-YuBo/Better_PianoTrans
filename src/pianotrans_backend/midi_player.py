"""Reusable MIDI timeline and FluidSynth playback primitives."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mido


@dataclass(frozen=True)
class MidiTimeline:
    duration: float
    segments: tuple[tuple[int, float, int], ...]
    total_ticks: int
    ticks_per_beat: int
    messages: tuple[tuple[int, mido.Message], ...]

    @classmethod
    def read(cls, path: str | Path) -> "MidiTimeline":
        midi_file = mido.MidiFile(str(path))
        tempo = 500000
        tick = 0
        seconds = 0.0
        segments: list[tuple[int, float, int]] = [(0, 0.0, tempo)]
        messages: list[tuple[int, mido.Message]] = []
        for message in mido.merge_tracks(midi_file.tracks):
            delta = int(message.time)
            seconds += mido.tick2second(delta, midi_file.ticks_per_beat, tempo)
            tick += delta
            messages.append((tick, message.copy(time=0)))
            if message.type == "set_tempo":
                tempo = message.tempo
                segments.append((tick, seconds, tempo))
        return cls(seconds, tuple(segments), tick, midi_file.ticks_per_beat, tuple(messages))

    def tick_to_seconds(self, tick: int) -> float:
        if not self.segments or self.total_ticks <= 0:
            return 0.0
        segment = self.segments[0]
        for candidate in self.segments:
            if candidate[0] > tick:
                break
            segment = candidate
        start_tick, start_seconds, tempo = segment
        value = start_seconds + mido.tick2second(max(0, tick - start_tick), self.ticks_per_beat, tempo)
        return min(self.duration, value)

    def seconds_to_tick(self, seconds: float) -> int:
        if seconds <= 0 or not self.segments:
            return 0
        for index, (start_tick, start_seconds, tempo) in enumerate(self.segments):
            next_seconds = self.segments[index + 1][1] if index + 1 < len(self.segments) else self.duration
            if seconds <= next_seconds:
                delta = max(0.0, seconds - start_seconds)
                return min(self.total_ticks, start_tick + round(mido.second2tick(delta, self.ticks_per_beat, tempo)))
        return self.total_ticks

    def create_seek_file(self, target_tick: int, directory: Path) -> Path:
        target_tick = min(self.total_ticks, max(0, int(target_tick)))
        current_tempo = 500000
        programs: dict[int, int] = {}
        controls: dict[tuple[int, int], int] = {}
        pitch_bends: dict[int, int] = {}
        active_notes: dict[tuple[int, int], list[int]] = {}
        for absolute_tick, message in self.messages:
            if absolute_tick >= target_tick:
                break
            if message.type == "set_tempo":
                current_tempo = message.tempo
            elif message.type == "program_change":
                programs[message.channel] = message.program
            elif message.type == "control_change":
                controls[(message.channel, message.control)] = message.value
            elif message.type == "pitchwheel":
                pitch_bends[message.channel] = message.pitch
            elif message.type == "note_on" and message.velocity > 0:
                active_notes.setdefault((message.channel, message.note), []).append(message.velocity)
            elif message.type in {"note_off", "note_on"}:
                key = (message.channel, message.note)
                if active_notes.get(key):
                    active_notes[key].pop(0)
                    if not active_notes[key]:
                        del active_notes[key]
        track: list[mido.Message] = [mido.MetaMessage("set_tempo", tempo=current_tempo, time=0)]
        for channel, program in sorted(programs.items()):
            track.append(mido.Message("program_change", channel=channel, program=program, time=0))
        for (channel, control), value in sorted(controls.items()):
            track.append(mido.Message("control_change", channel=channel, control=control, value=value, time=0))
        for channel, pitch in sorted(pitch_bends.items()):
            track.append(mido.Message("pitchwheel", channel=channel, pitch=pitch, time=0))
        for (channel, note), velocities in sorted(active_notes.items()):
            for velocity in velocities:
                track.append(mido.Message("note_on", channel=channel, note=note, velocity=velocity, time=0))
        previous_tick = target_tick
        for absolute_tick, message in self.messages:
            if absolute_tick < target_tick:
                continue
            track.append(message.copy(time=absolute_tick - previous_tick))
            previous_tick = absolute_tick
        if track[-1].type != "end_of_track":
            track.append(mido.MetaMessage("end_of_track", time=0))
        midi_file = mido.MidiFile(type=0, ticks_per_beat=self.ticks_per_beat)
        midi_file.tracks.append(mido.MidiTrack(track))
        directory.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="midi-seek-", suffix=".mid", dir=directory)
        os.close(handle)
        path = Path(name)
        midi_file.save(path)
        return path
