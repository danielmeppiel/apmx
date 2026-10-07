"""Bridge: wire real chain execution into the live Textual app.

Lives outside ``src/apmx/tui/`` on purpose. ``tests/unit/tui/test_architecture.py``
guards every ``tui/*.py`` module against importing the real engine/chain/process
stack, so the one module allowed to import both ``FactoryApp`` and
``apmx.contracts.chain`` has to live elsewhere. The canonical engine and
``ContractLogger`` still own every semantic decision here: this module never
invents an outcome, a transcript line, or a redaction choice. It only takes the
exact same ``RunEvent``/``ChainResult`` values ``ContractLogger`` already
records and additionally presents them, read-only, to a running ``FactoryApp``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding

from ..contracts.models import ChainResult, ContractError, Outcome, RunEvent
from ..core.contract_logger import ContractLogger
from ..tui.app import FactoryApp, _check_finished_fields
from ..tui.graph import build_nodes

if TYPE_CHECKING:
    from pathlib import Path

    from ..contracts.resolution import ChainPlan, Graph


def _attach_live(
    logger: ContractLogger,
    *,
    identities: tuple[str, ...],
    app: LiveFactoryApp,
    state: dict[str, str | None],
) -> LiveContractLogger:
    """Promote an already-constructed ``ContractLogger`` in place.

    ``ContractLogger.new_leaf``/``new_attempt`` construct a plain
    ``ContractLogger`` to share state (``_display``, ``_step``) exactly as the
    non-TUI path does. Rebinding the class onto that already-constructed
    instance (instead of duplicating its construction here) guarantees this
    bridge can never drift from whatever the base class decides those
    methods should carry over; it is the same transplant used once, up
    front, to attach the chain-level logger too.
    """
    logger.__class__ = LiveContractLogger
    live = logger  # now satisfies the subclass's attribute contract below
    live._tui_identities = identities  # type: ignore[attr-defined]
    live._tui_app = app  # type: ignore[attr-defined]
    live._tui_state = state  # type: ignore[attr-defined]
    return live  # type: ignore[return-value]


class LiveContractLogger(ContractLogger):
    """Forward the real event stream into a running ``FactoryApp``.

    ``on_event``/``chain_node`` always call ``super()`` first: the transcript,
    redaction and bounded-debug-exclusion behaviour is completely unchanged.
    The UI presentation is a side effect appended afterwards, never a
    replacement for what ``ContractLogger`` already decided to retain.
    """

    _tui_identities: tuple[str, ...]
    _tui_app: LiveFactoryApp
    _tui_state: dict[str, str | None]

    def new_leaf(self, *, index: int, count: int, contract: Path) -> LiveContractLogger:
        return _attach_live(
            super().new_leaf(index=index, count=count, contract=contract),
            identities=self._tui_identities,
            app=self._tui_app,
            state=self._tui_state,
        )

    def new_attempt(self, *, index: int, count: int) -> LiveContractLogger:
        return _attach_live(
            super().new_attempt(index=index, count=count),
            identities=self._tui_identities,
            app=self._tui_app,
            state=self._tui_state,
        )

    def chain_node(
        self, index: int, count: int, contract: Path, *, catalog: tuple[Path, ...] = ()
    ) -> None:
        super().chain_node(index, count, contract, catalog=catalog)
        if 1 <= index <= len(self._tui_identities):
            identity = self._tui_identities[index - 1]
            self._tui_state["current"] = identity
            self._tui_app.call_from_thread(self._tui_app.apply_card_status, identity, "running")

    def on_event(self, event: RunEvent) -> None:
        super().on_event(event)
        self._tui_app.call_from_thread(self._present, event)

    def _present(self, event: RunEvent) -> None:
        """Presentation only: every outcome word here already came from the
        canonical engine/ContractLogger dispatch above, never recomputed."""
        app = self._tui_app
        if event.kind == "native_diagnostic":
            # Bounded, excluded-from-transcript debug noise stays in the
            # separate diagnostics pane, matching ContractLogger's own
            # _native_diagnostic/_diagnostic split.
            app.append_diagnostic(str(event.data.get("text", "")))
            return
        app.append_event(event)
        identity = self._tui_state.get("current")
        if identity is None:
            return
        if event.kind == "check_started":
            app.apply_card_status(identity, "checking")
        elif event.kind == "check_finished":
            _name, status = _check_finished_fields(event)
            app.apply_card_status(identity, "running" if status == "pass" else "retrying")
        elif event.kind == "finished":
            result = event.data.get("result")
            outcome = getattr(result, "outcome", None)
            if outcome is Outcome.COMPLETE:
                app.apply_card_status(identity, "passed")
            elif outcome is not None:
                app.apply_card_status(identity, "failed")


class LiveFactoryApp(FactoryApp):
    """``FactoryApp`` with a real run driving it in a background thread.

    The run executes in a worker thread so Textual keeps the main thread's
    event loop; cancellation is a cooperative ``threading.Event`` passed as
    ``cancel_requested`` down through ``run_chain``/``supervise_process`` --
    the same mechanism already used for the non-UI cancellation tests, not a
    second one.
    """

    TITLE = "APMX factory (live)"
    BINDINGS: ClassVar[list[Binding]] = [
        *FactoryApp.BINDINGS,
        Binding("c", "cancel_run", "Cancel", show=True),
    ]

    def __init__(
        self, graph: Graph, *, runner: Callable[[Callable[[], bool]], ChainResult]
    ) -> None:
        super().__init__(graph)
        self._cancel_event = threading.Event()
        self._runner = runner
        self.chain_result: ChainResult | None = None
        self.run_failure: BaseException | None = None

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def action_cancel_run(self) -> None:
        if self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.append_diagnostic("Cancel requested; waiting for the current step to stop.")

    def on_mount(self) -> None:
        super().on_mount()
        threading.Thread(target=self._run, name="apmx-tui-chain", daemon=True).start()

    def _run(self) -> None:
        try:
            self.chain_result = self._runner(self.is_cancel_requested)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller, never swallowed
            self.run_failure = exc
        finally:
            self.call_from_thread(self.exit)


def _print_final_summary(result: ChainResult) -> None:
    """Print the authoritative plain-text outcome after the TUI has exited.

    ``launch_live`` disables ``ContractLogger``'s own live terminal echo so
    Textual can own the alt-screen; nothing else replaces that echo once the
    screen is released, so without this the operator sees nothing at all
    about what happened. Every value below is read straight off the already
    -recorded ``ChainResult``/``RunResult`` -- this never recomputes or
    reinterprets an outcome, only reports the one the engine already
    decided.
    """
    passed = sum(1 for run in result.runs if run.outcome is Outcome.COMPLETE)
    total = len(result.runs)
    print()
    print(f"apmx factory: {result.outcome.name} ({passed}/{total} contracts passed)")
    if result.stop_reason:
        print(f"  stopped: {result.stop_reason}")
    for run in result.runs:
        if run.outcome is not Outcome.COMPLETE:
            print(f"  {run.outcome.name}: {run.run_directory}")
    print(f"  record: {result.record_path}")


def launch_live(
    closure: ChainPlan,
    *,
    logger: ContractLogger,
    allow_advisory: bool,
    consent_source: str,
) -> ChainResult:
    """Run the real chain live, presenting it through ``FactoryApp``.

    Consent for this run was already obtained on the plain terminal by the
    caller (``invoke_contract``'s existing default-No ``confirm_factory()``
    gate, before this function is ever reached); this only wires the already
    -authorized execution into the Textual surface.
    """
    from ..contracts.chain import run_chain

    identities = tuple(node.identity for node in build_nodes(closure.graph))
    app_holder: dict[str, LiveFactoryApp] = {}

    def runner(cancel_requested: Callable[[], bool]) -> ChainResult:
        live_logger = _attach_live(
            logger,
            identities=identities,
            app=app_holder["app"],
            state={"current": None},
        )
        live_logger._display.disable()  # stop echoing to the terminal Textual now owns
        return run_chain(
            closure,
            logger=live_logger,
            allow_advisory=allow_advisory,
            consent_source=consent_source,
            cancel_requested=cancel_requested,
        )

    app = LiveFactoryApp(closure.graph, runner=runner)
    app_holder["app"] = app
    app.run()
    if app.run_failure is not None:
        raise app.run_failure
    if app.chain_result is None:
        raise ContractError("The live TUI exited before the run finished.", code="tui_interrupted")
    _print_final_summary(app.chain_result)
    return app.chain_result
