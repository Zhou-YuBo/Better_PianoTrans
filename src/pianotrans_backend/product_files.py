"""Verify only files needed by a requested playback mode."""

from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactVerification, verify_file
from .product_contract import EntryKind, EntryRef
from .product_store import ProductRepository, StaleRevision


class FileResolutionError(ValueError):
    pass


class ProductFileService:
    def __init__(self, store: ProductRepository) -> None:
        self.store = store

    def _entry(self, ref: EntryRef) -> dict:
        if ref.kind == EntryKind.TASK:
            row = self.store.get_work_item(ref.id)
            if row is None:
                raise FileResolutionError("work item not found")
            return {"audio_path": row["inputPath"], "audio_sha256": None,
                    "revision": row["revision"]}
        if ref.kind == EntryKind.HISTORY:
            row = self.store.get_attempt(ref.id)
            if row is None or row["status"] != "succeeded":
                raise FileResolutionError("history result cannot be played")
            return {"audio_path": row["input_path"], "audio_sha256": row["input_sha256"],
                    "midi_path": row["midi_path"], "midi_sha256": row["output_sha256"],
                    "revision": row["revision"]}
        if ref.kind == EntryKind.FAVORITE:
            row = self.store.get_favorite(ref.id)
            if row is None:
                raise FileResolutionError("favorite not found")
            return {"audio_path": row["input_path"], "audio_sha256": row["input_sha256"],
                    "midi_path": row["midi_path"], "midi_sha256": row["output_sha256"],
                    "revision": row["revision"]}
        raise FileResolutionError("unknown entry kind")

    def check(self, ref: EntryRef, mode: str) -> dict:
        if mode not in ("audio", "midi", "paired"):
            raise ValueError("unknown playback mode")
        entry = self._entry(ref)
        if ref.kind == EntryKind.TASK and mode != "audio":
            raise FileResolutionError("unsubmitted audio has no MIDI")
        required = ("audio", "midi") if mode == "paired" else (mode,)
        checks = {}

        def verify(kind: str) -> None:
            path = Path(entry[f"{kind}_path"])
            expected = entry[f"{kind}_sha256"]
            if ref.kind == EntryKind.TASK:
                checks[kind] = "available" if path.is_file() else "missing"
            else:
                checks[kind] = verify_file(path, expected).status.value

        for kind in required:
            verify(kind)
        if ref.kind != EntryKind.TASK and any(checks[kind] != "match" for kind in required):
            for kind in ("audio", "midi"):
                if kind not in checks:
                    verify(kind)
        actions = ["cancel"]
        if checks.get("audio") == "missing" and checks.get("midi") == "match" and mode == "paired":
            actions.append("midi_only")
        if checks.get("midi") in ("missing", "mismatch") and checks.get("audio") == "match":
            actions.append("recover_result")
        if any(status in ("missing", "mismatch") for status in checks.values()):
            actions.append("reassign" if ref.kind == EntryKind.TASK else "relocate")
        if any(status == "unverified" for status in checks.values()):
            actions.append("confirm_unverified_once")
        ready = all(checks[kind] in ("match", "available") for kind in required)
        success = self.store.find_success(entry["audio_sha256"]) if ref.kind != EntryKind.TASK and entry["audio_sha256"] else None
        return {"entryRef": {"kind": ref.kind.value, "id": ref.id}, "mode": mode,
                "revision": entry["revision"], "checks": checks, "ready": ready,
                "allowedActions": actions, "successRecordId": success["successRecordId"] if success else None,
                "paths": {kind: entry[f"{kind}_path"] for kind in required}}

    def relocate(self, ref: EntryRef, kind: str, new_path: Path, expected_revision: int) -> dict:
        if kind not in ("audio", "midi") or ref.kind == EntryKind.TASK:
            raise FileResolutionError("relocation requires committed history or favorite")
        entry = self._entry(ref)
        if entry["revision"] != expected_revision:
            raise StaleRevision(ref.id)
        expected = entry[f"{kind}_sha256"]
        if not expected:
            raise FileResolutionError("stored fingerprint is missing")
        if verify_file(new_path, expected).status != ArtifactVerification.MATCH:
            raise FileResolutionError("selected file does not match stored fingerprint")
        table = "history_attempts" if ref.kind == EntryKind.HISTORY else "favorites"
        id_column = "attempt_id" if ref.kind == EntryKind.HISTORY else "favorite_id"
        path_column = "input_path" if kind == "audio" else "midi_path"
        with self.store.db:
            result = self.store.db.execute(f"""UPDATE {table} SET {path_column}=?,revision=revision+1
                WHERE {id_column}=? AND revision=?""", (str(new_path.resolve()), ref.id, expected_revision))
            if result.rowcount != 1:
                raise StaleRevision(ref.id)
        return self.store.get_attempt(ref.id) if ref.kind == EntryKind.HISTORY else self.store.get_favorite(ref.id)
