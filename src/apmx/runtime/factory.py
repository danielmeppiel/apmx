from ..contracts.events import EventEmitter
from ..contracts.models import ContractLimits
from ..contracts.stream import ContractStreamDecoder
from .copilot_runtime import CopilotRuntime
from .opencode_runtime import OpenCodeRuntime
from .opencode_stream import OpenCodeStreamDecoder
from .replay import ReplayRuntime


class RuntimeFactory:
    @staticmethod
    def get_runtime_by_name(
        name: str, model: str | None = None
    ) -> CopilotRuntime | OpenCodeRuntime | ReplayRuntime:
        if name == "copilot":
            return CopilotRuntime()
        if name == "replay":
            return ReplayRuntime()
        if name == "opencode":
            return OpenCodeRuntime()
        raise ValueError(f"Unsupported native runtime: {name}")

    @staticmethod
    def get_contract_decoder(
        name: str, events: EventEmitter, *, limits: ContractLimits
    ) -> ContractStreamDecoder:
        if name in ("copilot", "replay"):
            return ContractStreamDecoder(events, limits=limits)
        if name == "opencode":
            return OpenCodeStreamDecoder(events, limits=limits)
        raise ValueError(f"Unsupported native runtime: {name}")
