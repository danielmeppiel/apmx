"""Bounded read models for observations, never a second execution/evidence owner."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.models import Artifact, CheckObservation, FileEntry, RunEvent, RunResult

EVENT_LIMIT = 1200


@dataclass
class AttemptView:
    identity: str
    run_id: str
    index: int
    limit: int
    phase: str = "starting"
    elapsed: float = 0
    inputs: list[tuple[FileEntry, str]] = field(default_factory=list)
    workspace_files: int | None = None
    checks: dict[str, CheckObservation | None] = field(default_factory=dict)
    result: RunResult | None = None
    directory: Path | None = None


@dataclass(frozen=True)
class OutputItem:
    identity: str
    attempt: int
    artifact: Artifact
    result: RunResult

    @property
    def key(self) -> tuple[str, int, str]:
        return self.identity, self.attempt, self.artifact.relative_path


class Observations:
    def __init__(self) -> None:
        self.events: deque[tuple[str | None, RunEvent]] = deque(maxlen=EVENT_LIMIT)
        self.attempts: dict[tuple[str, str], AttemptView] = {}
        self.omitted = 0

    def append(self, identity: str | None, event: RunEvent) -> AttemptView | None:
        if len(self.events) == EVENT_LIMIT:
            self.omitted += 1
        self.events.append((identity, event))
        if identity is None:
            return None
        key = identity, event.run_id
        attempt = self.attempts.setdefault(
            key,
            AttemptView(identity, event.run_id, event.context.attempt, event.context.attempt_limit),
        )
        attempt.elapsed = max(attempt.elapsed, event.elapsed_seconds)
        if event.kind == "selected":
            directory = event.data.get("run_directory")
            if isinstance(directory, str):
                attempt.directory = Path(directory)
        elif event.kind == "phase":
            attempt.phase = str(event.data.get("name", "unknown"))
        elif event.kind == "input_captured":
            entry = event.data.get("entry")
            if isinstance(entry, FileEntry):
                attempt.inputs.append((entry, str(event.data.get("origin", "unknown"))))
        elif event.kind == "workspace_captured":
            count = event.data.get("files")
            if isinstance(count, int):
                attempt.workspace_files = count
        elif event.kind == "check_started":
            attempt.checks[str(event.data.get("name", "unknown"))] = None
        elif event.kind == "check_finished":
            observation = event.data.get("observation")
            if isinstance(observation, CheckObservation):
                attempt.checks[observation.name] = observation
        elif event.kind in ("attempt_finished", "finished"):
            result = event.data.get("result")
            if isinstance(result, RunResult):
                attempt.result = result
                attempt.directory = result.run_directory
        return attempt
