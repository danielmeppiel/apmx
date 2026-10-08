"""Command/UI coordination. Canonical command owns execution, cleanup and delivery."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import click
from textual.app import App
from textual.binding import Binding
from textual.widgets import Button, Static

from ..contracts.models import ChainResult, ContractError, Outcome, RunEvent, RunResult
from ..contracts.resolution import ChainPlan, Graph
from ..contracts.stream import safe_text
from ..core.contract_logger import ContractLogger
from ..tui.app import FactoryApp
from ..tui.consent import ConsentScreen
from ..tui.graph import build_nodes
from ..utils.console import tui_reduced_motion


def _fire_and_forget(app, callback: Callable[..., object], *args: object) -> None:
    """Never block a command thread on a stopped presentation loop."""
    loop = getattr(app, "_loop", None)
    if loop is None:
        if not isinstance(app, App):
            callback(*args)
        return
    if loop.is_closed() or not loop.is_running():
        return
    try:
        if isinstance(app, App):
            # The message pump restores Textual's ContextVars before creating widgets.
            loop.call_soon_threadsafe(app.call_later, callback, *args)
        else:
            loop.call_soon_threadsafe(callback, *args)
    except RuntimeError:
        if not loop.is_closed():
            raise


class LivePresentation:
    def __init__(self, app, identities: tuple[str, ...] = ()) -> None:
        self.app = app
        self.identities = identities

    def event(self, event: RunEvent) -> None:
        # Text is sanitized before dispatch; typed observations/results are not parsed.
        event = replace(
            event,
            data={
                key: safe_text(value)
                if isinstance(value, str) and key in {"text", "message"}
                else value
                for key, value in event.data.items()
            },
        )
        index = event.context.contract_index
        identity = self.identities[index - 1] if index and index <= len(self.identities) else None
        _fire_and_forget(self.app, self._present, identity, event)

    def _present(self, identity: str | None, event: RunEvent) -> None:
        if event.kind == "native_diagnostic":
            self.app.append_diagnostic(str(event.data.get("text", "")))
            return
        self.app.append_event(event)
        if identity is None:
            return
        if event.kind == "selected":
            self.app.apply_card_status(identity, "running")
        elif event.kind == "check_started":
            self.app.apply_card_status(identity, "checking")
        elif event.kind == "attempt_finished":
            result = event.data.get("result")
            if isinstance(result, RunResult):
                self.app.apply_card_status(
                    identity,
                    "passed"
                    if result.outcome is Outcome.COMPLETE
                    else "cancelled"
                    if result.stop_reason == "cancelled"
                    else "failed",
                )

    def message(self, text: str) -> None:
        if hasattr(self.app, "append_message"):
            _fire_and_forget(self.app, self.app.append_message, text)

    def graph(self, graph: Graph) -> None:
        self.identities = tuple(n.identity for n in build_nodes(graph))
        _fire_and_forget(self.app, self.app.set_graph, graph)

    def phase(self, phase: str) -> None:
        if hasattr(self.app, "set_phase"):
            _fire_and_forget(self.app, self.app.set_phase, phase)

    def confirm(self, prompt: str) -> bool:
        response = threading.Event()
        answer = [False]
        _fire_and_forget(self.app, self.app.request_consent, prompt, response, answer)
        # The command remains cancellable without a blocking Textual call_from_thread.
        while not response.wait(0.1):
            if self.app.is_cancel_requested():
                return False
        return answer[0]

    def result(self, result: ChainResult | RunResult) -> None:
        if isinstance(result, ChainResult):
            _fire_and_forget(self.app, self.app.show_result, result)

    def delivery(self, path: Path | None, error: str | None = None) -> None:
        def present() -> None:
            self.app.delivery_path = path
            self.app.delivery_state = (
                f"FAILED (exit 23): {safe_text(error)}"
                if error is not None
                else "Delivered; unsigned"
                if path
                else "Not applicable (no retained official APM inventory)"
            )
            self.app._views_dirty = True

        _fire_and_forget(self.app, present)


def _attach_live(
    logger: ContractLogger,
    *,
    identities: tuple[str, ...],
    app,
    state: dict[str, str | None] | None = None,
) -> ContractLogger:
    """Attach without changing logger type or sharing a mutable current identity."""
    logger.presentation = LivePresentation(app, identities)
    return logger


class LiveFactoryApp(FactoryApp):
    """Run a complete command off the UI loop; remain until explicit operator exit."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("c", "cancel_run", "Cancel"),
        Binding("r", "review_consent", "Decision", show=False),
        Binding("y", "consent_yes", "Approve", show=False),
        Binding("n", "consent_no", "Decline", show=False),
        Binding("ctrl+c", "cancel_run", "Cancel", show=False, priority=True),
    ]

    def __init__(
        self,
        graph: Graph | None = None,
        *,
        runner: Callable[[Callable[[], bool]], ChainResult] | None = None,
        command: Callable[[LivePresentation, Callable[[], bool]], None] | None = None,
        planning: bool = False,
        source: str = "",
        headless_auto_exit: bool = False,
    ) -> None:
        super().__init__(graph, reduced_motion=tui_reduced_motion(), source=source)
        self._cancel_event = threading.Event()
        self._runner = runner
        self._command = command
        self._planning = planning
        self._headless_auto_exit = headless_auto_exit
        self.chain_result: ChainResult | None = None
        self.run_failure: BaseException | None = None
        self._worker: threading.Thread | None = None
        self._done = threading.Event()
        self._exit_when_done = False
        self._consent: tuple[str, threading.Event, list[bool]] | None = None
        self.presentation = LivePresentation(
            self,
            tuple(n.identity for n in build_nodes(graph)) if graph else (),
        )

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def action_cancel_run(self) -> None:
        if self._done.is_set() or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.set_phase("Cancelling / waiting for cleanup")
        self.append_message("Cancel requested. Waiting for canonical preparation/process cleanup.")
        if isinstance(self.screen, ConsentScreen):
            self.screen.dismiss(False)
        elif self._consent is not None:
            self._answer_execution(False)

    def action_quit(self) -> None:
        if self.command_code is not None:
            self.exit()
            return

        def decision(answer: bool | None) -> None:
            if answer:
                self._exit_when_done = True
                if self.command_code is not None:
                    self.exit()
                else:
                    self.action_cancel_run()

        self.push_screen(
            ConsentScreen(
                "Cancel this command and quit after cleanup?\nNo leaves it running. "
                "Use c to cancel and keep results open.",
                accept_label="Yes, cancel and wait",
            ),
            decision,
        )

    def request_consent(
        self,
        prompt: str,
        response: threading.Event,
        answer: list[bool],
    ) -> None:
        self._consent = prompt, response, answer
        preparation = "Load " in prompt
        self.set_phase("Approve preparation" if preparation else "Approve execution")
        self.action_review_consent()

    def _answer_execution(self, value: bool) -> None:
        if self._consent is None:
            return
        _, response, answer = self._consent
        answer[0] = value and not self.is_cancel_requested()
        self._consent = None
        self.query_one("#approval").remove_class("-visible")
        self.screen_stack[0].remove_class("-approving")
        self.query_one("#execution-no", Button).disabled = True
        self.query_one("#execution-yes", Button).disabled = True
        response.set()

    def action_consent_yes(self) -> None:
        if self._consent and "Load " not in self._consent[0]:
            self._answer_execution(True)
        else:
            self.action_copy_path()

    def action_consent_no(self) -> None:
        if self._consent and "Load " not in self._consent[0]:
            self._answer_execution(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("execution-no", "execution-yes"):
            self._answer_execution(event.button.id == "execution-yes")

    def action_review_consent(self) -> None:
        if self._consent is None or isinstance(self.screen, ConsentScreen):
            return
        prompt, response, answer = self._consent
        preparation = "Load " in prompt
        if not preparation:
            self.query_one("#approval").add_class("-visible")
            self.screen_stack[0].add_class("-approving")
            name = self._graph.root.name if self._graph else self.source
            self.query_one("#approval-text", Static).update(
                f"{safe_text(name)} | {prompt}\n"
                "Local, not sandboxed: host files, network, logins; model usage can cost money.\n"
                "Checked native handoffs remain unproven. Inspect graph first; No is default."
            )
            self.query_one("#execution-no", Button).disabled = False
            self.query_one("#execution-yes", Button).disabled = False
            self.query_one("#execution-no", Button).focus()
            return
        disclosure = (
            "Load only trusted sources. APM may install dependencies using host files, "
            "network and available logins. No agent or check runs yet."
            if preparation
            else "Local, not sandboxed. Agent and checks can use host files, network and logins. "
            "Model usage may cost money. Checked native handoffs remain unproven."
        )

        def decision(value: bool | None) -> None:
            if value is None:
                return
            answer[0] = value and not self.is_cancel_requested()
            self._consent = None
            response.set()

        self.push_screen(
            ConsentScreen(
                f"Source: {safe_text(self.source)}\n\n{prompt}\n\n{disclosure}",
                accept_label="Yes, prepare" if preparation else "Yes, execute",
                allow_inspect=True,
            ),
            decision,
        )

    def on_mount(self) -> None:
        super().on_mount()
        self._worker = threading.Thread(target=self._run, name="apmx-tui-command", daemon=False)
        self._worker.start()

    def _run(self) -> None:
        code = 0
        try:
            if self._command is not None:
                self._command(self.presentation, self.is_cancel_requested)
            elif self._runner is not None:
                self.chain_result = self._runner(self.is_cancel_requested)
                code = int(self.chain_result.outcome)
                _fire_and_forget(self, self.show_result, self.chain_result)
        except click.exceptions.Exit as exc:
            code = exc.exit_code
        except BaseException as exc:  # noqa: BLE001 - retained for inspection, re-raised by launcher
            self.run_failure = exc
            code = int(Outcome.HALTED)
            self.presentation.message(f"Command failed: {safe_text(str(exc))}")
        finally:
            self._done.set()
            _fire_and_forget(self, self._completed, code)

    def _completed(self, code: int) -> None:
        self.finish_command(code, planning=self._planning)
        self.refresh_bindings()
        if self._exit_when_done or self._headless_auto_exit:
            self.exit()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        return not (action == "cancel_run" and self.command_code is not None)


def launch_workspace(
    *,
    source: str,
    entry: str,
    planning: bool,
    command: Callable[[LivePresentation, Callable[[], bool]], None],
) -> int:
    app = LiveFactoryApp(source=f"{source} / entry {entry}", command=command, planning=planning)
    try:
        app.run()
    finally:
        if app._worker is not None and app._worker.is_alive():
            app._cancel_event.set()
            app._worker.join()
    if app.run_failure is not None:
        raise app.run_failure
    if app.command_code is None:
        raise ContractError("TUI closed before command completion.", code="tui_interrupted")
    return app.command_code


def launch_live(
    closure: ChainPlan,
    *,
    logger: ContractLogger,
    allow_advisory: bool,
    consent_source: str,
    headless: bool = False,
) -> ChainResult:
    """Compatibility entry for already-authorized callers; CLI uses launch_workspace."""
    from ..contracts.chain import run_chain

    app: LiveFactoryApp

    def runner(cancel_requested: Callable[[], bool]) -> ChainResult:
        _attach_live(
            logger, identities=tuple(n.identity for n in build_nodes(closure.graph)), app=app
        )
        logger._display.disable()
        return run_chain(
            closure,
            logger=logger,
            allow_advisory=allow_advisory,
            consent_source=consent_source,
            cancel_requested=cancel_requested,
        )

    app = LiveFactoryApp(closure.graph, runner=runner, headless_auto_exit=headless)
    app.run(headless=headless)
    if app._worker:
        app._worker.join()
    if app.run_failure:
        raise app.run_failure
    if app.chain_result is None:
        raise ContractError("TUI closed before execution finished.", code="tui_interrupted")
    return app.chain_result
