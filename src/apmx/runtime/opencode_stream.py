"""OpenCode 1.2.24 observations using the shared bounded diagnostic path."""

from ..contracts.context_layout import is_native_skill_name
from ..contracts.events import EventEmitter
from ..contracts.models import ContractLimits
from ..contracts.stream import ContractStreamDecoder


class OpenCodeStreamDecoder(ContractStreamDecoder):
    """Observe terminal model steps without inventing a native exit envelope."""

    def __init__(self, events: EventEmitter, *, limits: ContractLimits | None = None) -> None:
        super().__init__(events, limits=limits)
        self.session_id: str | None = None
        self._step_message: str | None = None
        self._last_reason: str | None = None
        self._finished_steps: dict[str, str] = {}

    def finish(self) -> None:
        if self._closed:
            return
        super().finish()
        self.completion_seen = (
            self.protocol_error is None
            and self._step_message is None
            and self._last_reason == "stop"
        )
        if self.completion_seen:
            self._emit(
                "metadata",
                text=(
                    "Native final model step stopped; completion also requires "
                    "successful process termination. No native exit envelope was reported."
                ),
            )

    def _event(self, event: dict) -> None:
        session = event.get("sessionID")
        if not self._identifier(session):
            self._protocol_failure("OpenCode event is missing a valid sessionID.")
            return
        if self.session_id is not None and self.session_id != session:
            self._protocol_failure("OpenCode events disagree on the invocation session.")
            return
        self.session_id = session
        kind = event["type"]
        if kind == "error":
            self._error(event)
            return
        if kind == "reasoning":
            return
        types = {
            "text": "text",
            "tool_use": "tool",
            "step_start": "step-start",
            "step_finish": "step-finish",
        }
        if kind not in types:
            self._dispatch(kind, {})
            return
        part = event.get("part")
        if (
            not isinstance(part, dict)
            or part.get("type") != types[kind]
            or part.get("sessionID") != session
            or not self._identifier(part.get("id"))
            or not self._identifier(part.get("messageID"))
        ):
            self._protocol_failure("OpenCode event has inconsistent part identity.")
            return
        if kind == "step_start":
            if self._step_message is not None:
                self._protocol_failure(
                    "OpenCode started a model step before finishing the prior step."
                )
            self._step_message = part["messageID"]
            self._last_reason = None
        elif kind == "step_finish":
            self._step_finished(part)
        elif kind == "text":
            text = part.get("text")
            if not isinstance(text, str):
                self._protocol_failure("OpenCode text event has no public text.")
            else:
                self._bounded_activity(text)
        else:
            self._tool(part)

    @staticmethod
    def _identifier(value: object) -> bool:
        return isinstance(value, str) and 0 < len(value) <= 256

    def _step_finished(self, part: dict) -> None:
        reason = part.get("reason")
        if reason not in ("stop", "tool-calls"):
            self._protocol_failure("OpenCode model step did not finish normally.")
            return
        identifier = part["id"]
        if identifier in self._finished_steps:
            if self._finished_steps[identifier] != reason:
                self._protocol_failure("OpenCode reported conflicting model-step completion.")
            return
        if self._step_message != part["messageID"]:
            self._protocol_failure("OpenCode model-step completion has no matching start.")
            return
        if len(self._finished_steps) >= 256:
            self._protocol_failure("OpenCode model-step count exceeded the metadata limit.")
            return
        self._finished_steps[identifier] = reason
        self._step_message = None
        self._last_reason = reason

    def _tool(self, part: dict) -> None:
        name = part.get("tool")
        state = part.get("state")
        if (
            not self._identifier(name)
            or not isinstance(state, dict)
            or state.get("status") not in ("completed", "error")
        ):
            self._protocol_failure("OpenCode tool event has no terminal observation.")
            return
        failed = state["status"] == "error"
        self._activity(
            f"Tool {'failed' if failed else 'completed'}: {name}",
            tool_status="failed" if failed else "completed",
        )
        if failed:
            self._protocol_failure("An OpenCode native tool reported failure.")
        elif name == "skill":
            arguments = state.get("input")
            skill = arguments.get("name") if isinstance(arguments, dict) else None
            if is_native_skill_name(skill):
                self._skill_invoked({"name": skill})

    def _error(self, event: dict) -> None:
        self._protocol_failure(
            "OpenCode reported a session error, regardless of process exit code."
        )
        error = event.get("error")
        if not isinstance(error, dict):
            return
        name = error.get("name")
        data = error.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        if self._identifier(name) and isinstance(message, str):
            if len(message.encode("utf-8", errors="surrogatepass")) > 16 * 1024:
                message = "Native error detail exceeded the text limit."
            self._emit(
                "diagnostic",
                severity="error",
                message=f"Native OpenCode error ({name}): {message}",
                action="Check native authentication, model availability and OpenCode configuration.",
            )
