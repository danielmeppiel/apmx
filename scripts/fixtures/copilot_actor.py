"""Hermetic Copilot JSONL protocol actor; never performs model inference."""

import json
import os
import sys
from pathlib import Path


def emit(kind, **data):
    print(json.dumps({"type": kind, "data": data}), flush=True)


def main():
    with Path(os.environ["APMX_ACTOR_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"argv": sys.argv[1:], "cwd": str(Path.cwd())}) + "\n")
    if sys.argv[1:] == ["--version"]:
        print("copilot hermetic-protocol-fixture 1.0")
        return 0
    if sys.argv[-3:] == ["mcp", "list", "--json"]:
        print(json.dumps({"mcpServers": {}}))
        return 0
    if "-p" not in sys.argv:
        raise RuntimeError("Unexpected hermetic actor invocation")
    mode = os.environ["APMX_ACTOR_MODE"]
    for phase, text in (
        ("commentary", "Hermetic fixture progress.\n"),
        ("analysis", "PRIVATE_REASONING_SENTINEL\n"),
        ("final_answer", "Hermetic fixture finished.\n"),
    ):
        emit("assistant.message_start", messageId=phase, phase=phase, model="fixture-model")
        emit("assistant.message_delta", messageId=phase, deltaContent=text)
        emit("assistant.message", messageId=phase, content=text, phase=phase)
    emit(
        "tool.execution_complete",
        toolCallId="fixture-tool",
        success=True,
        result={"content": "PRIVATE_TOOL_SENTINEL"},
    )
    if mode != "halt":
        candidate = json.loads(Path("notes.md").read_text(encoding="utf-8"))
        if mode == "reject":
            candidate["value"] = -1
        Path("handoff.json").write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        Path("checks/check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    code = 7 if mode == "halt" else 0
    print(json.dumps({
        "type": "result", "exitCode": code, "sessionId": "hermetic-fixture", "usage": {},
    }), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
