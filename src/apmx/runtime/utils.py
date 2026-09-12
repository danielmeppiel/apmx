import os
import shutil
from pathlib import Path


def find_runtime_binary(name: str) -> str | None:
    binary = shutil.which(name + ".exe" if os.name == "nt" else name)
    if binary and Path(binary).suffix.lower() in {".cmd", ".bat"}:
        return None
    return binary
