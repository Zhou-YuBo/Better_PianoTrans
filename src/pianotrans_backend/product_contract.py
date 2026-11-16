"""Stable identities and command envelopes for the v1 application service."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


PROTOCOL_VERSION = "1.0"


class EntryKind(StrEnum):
    TASK = "task"
    HISTORY = "history"
    FAVORITE = "favorite"


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    PARTIAL = "partial"


@dataclass(frozen=True)
class EntryRef:
    kind: EntryKind
    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entry ID required")


@dataclass(frozen=True)
class CommandError:
    code: str
    user_message: str
    retryable: bool = False
    subject: EntryRef | None = None


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    outcome: Outcome
    revision: int | None = None
    affected_ids: tuple[str, ...] = ()
    item_results: tuple[dict, ...] = ()
    error: CommandError | None = None
    protocol_version: str = field(default=PROTOCOL_VERSION)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request ID required")
        if self.outcome == Outcome.REJECTED and self.error is None:
            raise ValueError("rejected result requires an error")
