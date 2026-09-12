from .copilot_runtime import CopilotRuntime


class RuntimeFactory:
    @staticmethod
    def get_runtime_by_name(name: str, model: str | None = None) -> CopilotRuntime:
        if name != "copilot":
            raise ValueError(f"Unsupported native runtime: {name}")
        return CopilotRuntime()
