"""Hermetic Copilot JSONL protocol actor; never performs model inference."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(kind, **data):
    print(json.dumps({"type": kind, "data": data}), flush=True)


def child():
    heartbeat = Path(os.environ["APMX_CHILD_HEARTBEAT"])
    Path(os.environ["APMX_CHILD_PID"]).write_text(str(os.getpid()), encoding="ascii")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and not Path(os.environ["APMX_CHILD_STOP"]).exists():
        heartbeat.write_text(str(time.monotonic_ns()), encoding="ascii")
        time.sleep(0.05)
    return 0


def start_child():
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-I", str(Path(__file__).resolve())]
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([*command, "--fixture-child"], env=env)
    deadline = time.monotonic() + 15
    while not Path(os.environ["APMX_CHILD_HEARTBEAT"]).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Hermetic child did not start")
        time.sleep(0.05)


def main():
    with Path(os.environ["APMX_ACTOR_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"argv": sys.argv[1:], "cwd": str(Path.cwd())}) + "\n")
    if sys.argv[1:] == ["--fixture-child"]:
        return child()
    if sys.argv[1:] == ["--version"]:
        print("copilot hermetic-protocol-fixture 1.0")
        return 0
    if sys.argv[-3:] == ["mcp", "list", "--json"]:
        print(json.dumps({"mcpServers": {}}))
        return 0
    if "-p" not in sys.argv:
        raise RuntimeError("Unexpected hermetic actor invocation")
    mode = os.environ["APMX_ACTOR_MODE"]
    prompt = sys.argv[sys.argv.index("-p") + 1]
    if os.environ.get("APMX_EXPECT_SKILL") == "1" and "RELEASE_SKILL_SENTINEL" not in prompt:
        raise RuntimeError("Selected packaged skill did not reach the native producer")
    phases = (
        ("commentary", "Hermetic fixture progress.\n"),
        ("analysis", "PRIVATE_REASONING_SENTINEL\n"),
        ("final_answer", "Hermetic fixture finished.\n"),
    )
    if mode == "quiet":
        phases = ()
        time.sleep(0.2)
    for phase, text in phases:
        emit("assistant.message_start", messageId=phase, phase=phase, model="fixture-model")
        emit("assistant.message_delta", messageId=phase, deltaContent=text)
        emit("assistant.message", messageId=phase, content=text, phase=phase)
        if phase == "commentary" and os.environ.get("APMX_STREAM_GATE"):
            deadline = time.monotonic() + 10
            while not Path(os.environ["APMX_STREAM_GATE"]).exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Public narration was not delivered before native completion")
                time.sleep(0.02)
    if mode != "quiet":
        emit(
            "tool.execution_complete",
            toolCallId="fixture-tool",
            success=True,
            result={"content": "PRIVATE_TOOL_SENTINEL"},
        )
    if mode not in {"halt", "linger"}:
        candidate = json.loads(Path("notes.md").read_text(encoding="utf-8"))
        if mode == "reject":
            candidate["value"] = -1
        Path("handoff.json").write_text(json.dumps(candidate) + "\n", encoding="utf-8")
        Path("checks/check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    if mode == "linger":
        start_child()
    code = 7 if mode == "halt" else 0
    print(json.dumps({
        "type": "result", "exitCode": code, "sessionId": "hermetic-fixture", "usage": {},
    }), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
