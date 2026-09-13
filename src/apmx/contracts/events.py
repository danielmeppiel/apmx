"""One ordered event handoff for a local invocation."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .models import EventSink, ImportedSkill, RunEvent

HEARTBEAT_SECONDS = 5

PreparationScope = Literal["package", "consumer"]


@dataclass(frozen=True)
class ApmInstallEvent:
    """Observed backend lifecycle, without argv, paths, environment or child text."""

    phase: Literal["started", "completed"]
    scope: PreparationScope
    version: str
    frozen: bool
    package_request: bool


@dataclass(frozen=True)
class ImportsSelectedEvent:
    """Validated context selected by the frontend, not the installed graph."""

    imports: tuple[ImportedSkill, ...]


PreparationEvent = ApmInstallEvent | ImportsSelectedEvent
PreparationSink = Callable[[PreparationEvent], None]


class EventEmitter:
    """Assign observation order in the conductor/supervisor thread."""

    def __init__(self, run_id: str, sink: EventSink) -> None:
        self.run_id = run_id
        self.sink = sink
        self.started = time.monotonic()
        self.sequence = 0

    def emit(
        self,
        kind: str,
        *,
        source: Literal["engine", "harness", "checker"] = "engine",
        **data: object,
    ) -> None:
        """Deliver one observation without deriving assessment outcomes."""
        self.sequence += 1
        self.sink(
            RunEvent(
                run_id=self.run_id,
                sequence=self.sequence,
                elapsed_seconds=time.monotonic() - self.started,
                kind=kind,
                source=source,
                data=data,
            )
        )
