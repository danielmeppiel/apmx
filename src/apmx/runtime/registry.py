from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeDescriptor:
    binary: str
    supports_contracts: bool


def get_runtime_descriptor(name: str) -> RuntimeDescriptor:
    if name == "copilot":
        return RuntimeDescriptor("copilot", True)
    if name == "codex":
        return RuntimeDescriptor("codex", False)
    raise ValueError(f"Unknown runtime: {name}")
