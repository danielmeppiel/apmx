from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeDescriptor:
    binary: str
    supports_contracts: bool
    # The harness reports no model, but apmx's explicit --model selects exactly that model
    # (OpenCode's -m provider/model). Policy may then fall back to the requested model.
    model_pinned_by_flag: bool = False


def get_runtime_descriptor(name: str) -> RuntimeDescriptor:
    if name == "copilot":
        return RuntimeDescriptor("copilot", True)
    if name == "opencode":
        return RuntimeDescriptor("opencode", True, model_pinned_by_flag=True)
    if name == "replay":
        return RuntimeDescriptor("apmx", True)
    if name == "codex":
        return RuntimeDescriptor("codex", False)
    raise ValueError(f"Unknown runtime: {name}")
