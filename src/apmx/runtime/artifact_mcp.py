"""Bounded stdio MCP bridge; dispatched by the packaged executable when frozen."""

import json
import os
import sys
from pathlib import Path
from typing import BinaryIO

from ..contracts import records, workspace
from ..contracts.git_export import ExportContext, delete_file, export_changes, write_file
from ..contracts.models import ContractError
from .artifact_tools import SERVER, SERVER_ENV, TOOLS, load_context, save_json


def _schema(name: str) -> dict:
    path_schema = {
        "type": "string",
        "description": "Portable workspace-relative file path, never an absolute path.",
    }
    fields = {"output": path_schema} if name == "export_changes" else {"path": path_schema}
    if name == "write_file":
        fields["content"] = {"type": "string"}
    descriptions = {
        "write_file": "Atomically write UTF-8 text in the private working copy. Protected checks and runner state cannot be edited.",
        "delete_file": "Delete one regular private working file, not a directory or caller file.",
        "export_changes": "Export actual source edits/additions/deletions as a deterministic Git patch into one explicitly declared artifact. Never hand-compose patch hunks. No shell; no caller changes.",
    }
    return {
        "name": name,
        "description": descriptions[name],
        "inputSchema": {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        },
    }


class ArtifactServer:
    def __init__(self, context: ExportContext) -> None:
        self.context = context
        self.state, _ = records._record_bytes(context.directory / "state.json", context.limits)
        self.initialized = False

    def save(self) -> None:
        save_json(self.context.directory / "state.json", self.state)

    def call(self, params: object) -> dict:
        try:
            if self.state["active"] is not None or self.state["failed"] is not False:
                raise ContractError(
                    "A prior artifact operation is incomplete or failed.", code="tool_incomplete"
                )
            if (
                not isinstance(params, dict)
                or not {"name", "arguments"} <= set(params) <= {"name", "arguments", "_meta"}
                or ("_meta" in params and not isinstance(params["_meta"], dict))
            ):
                raise ContractError("Expected a tool name and arguments.", code="tool_arguments")
            name, arguments = params["name"], params["arguments"]
            if name not in TOOLS or not isinstance(arguments, dict):
                raise ContractError("Unknown artifact tool.", code="tool_arguments")
            required = set(_schema(name)["inputSchema"]["required"])
            if set(arguments) != required:
                raise ContractError(
                    "Artifact tool arguments do not match its schema.", code="tool_arguments"
                )
            if self.state["operations"] >= 64:
                raise ContractError("Artifact operation limit reached.", code="tool_limit")
            self.state["active"] = name
            self.state["operations"] += 1
            self.save()
            if name == "write_file":
                result = write_file(self.context, arguments["path"], arguments["content"])
            elif name == "delete_file":
                result = delete_file(self.context, arguments["path"])
            else:
                for item in self.state["exports"]:
                    previous, _ = records._record_bytes(
                        self.context.directory / item["name"], self.context.limits
                    )
                    if previous["output"] == arguments["output"]:
                        raise ContractError(
                            "An artifact may be exported once per attempt.", code="export_repeated"
                        )
                result = export_changes(self.context, arguments["output"])
                filename = f"export-{len(self.state['exports']) + 1}.json"
                save_json(self.context.directory / filename, result)
                _, entry = workspace._read(
                    self.context.directory, filename, self.context.limits.file_bytes
                )
                self.state["exports"].append({"name": filename, "sha256": entry.sha256})
            self.state["active"] = None
            self.save()
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True)}]}
        except (ContractError, OSError, UnicodeError) as exc:
            self.state["failed"] = True
            self.state["error"] = self.state["error"] or (
                exc.code if isinstance(exc, ContractError) else "tool_io_failure"
            )
            self.save()
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}

    def serve(self, source: BinaryIO, destination: BinaryIO) -> int:
        def reply(identifier: object, *, result: object = None, error: dict | None = None) -> None:
            response = {"jsonrpc": "2.0", "id": identifier}
            response["error" if error else "result"] = error if error else result
            destination.write((json.dumps(response, ensure_ascii=True) + "\n").encode("ascii"))
            destination.flush()

        for _ in range(256):
            frame = source.readline(self.context.limits.frame_bytes + 1)
            if not frame:
                return 0
            identifier = None
            try:
                if len(frame) > self.context.limits.frame_bytes:
                    raise ValueError("Artifact protocol frame limit exceeded.")

                def unique(pairs: list[tuple[str, object]]) -> dict:
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError("Duplicate protocol field.")
                        value[key] = item
                    return value

                request = json.loads(frame, object_pairs_hook=unique)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                    raise ValueError("Expected a JSON-RPC 2.0 request.")
                identifier = request.get("id")
                method = request.get("method")
                if not isinstance(method, str) or (
                    identifier is not None and type(identifier) not in (int, str)
                ):
                    raise ValueError("Invalid protocol method or identifier.")
                if method == "initialize":
                    self.initialized = True
                    reply(
                        identifier,
                        result={
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": SERVER, "version": "0.1.0"},
                        },
                    )
                elif method in ("notifications/initialized", "notifications/cancelled"):
                    if method == "notifications/cancelled":
                        self.state["failed"] = True
                        self.state["error"] = "tool_cancelled"
                        self.save()
                elif method == "ping":
                    reply(identifier, result={})
                elif method == "tools/list" and self.initialized:
                    reply(identifier, result={"tools": [_schema(name) for name in TOOLS]})
                elif method == "tools/call" and self.initialized:
                    reply(identifier, result=self.call(request.get("params")))
                else:
                    reply(identifier, error={"code": -32601, "message": "Method unavailable."})
            except (ValueError, UnicodeError, RecursionError) as exc:
                self.state["failed"] = True
                self.state["error"] = "tool_protocol_error"
                self.save()
                reply(identifier, error={"code": -32600, "message": str(exc)})
                return 2
        self.state["failed"] = True
        self.state["error"] = "tool_protocol_limit"
        self.save()
        return 2


def run_server(config: Path) -> int:
    try:
        return ArtifactServer(load_context(config)).serve(sys.stdin.buffer, sys.stdout.buffer)
    except (ContractError, OSError, KeyboardInterrupt) as exc:
        sys.stderr.write(
            f"Artifact server stopped: {str(exc).encode('ascii', 'backslashreplace').decode('ascii')}\n"
        )
        return 2


if __name__ == "__main__":
    selected = os.environ.get(SERVER_ENV)
    if not selected:
        sys.stderr.write("Missing private artifact-server configuration.\n")
        raise SystemExit(2)
    raise SystemExit(run_server(Path(selected)))
