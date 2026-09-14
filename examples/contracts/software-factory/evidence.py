"""Read one fresh caller's evidence; never infer a latest run or certification."""

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FILE_LIMIT = 32 * 1024


class Stop(Exception):
    """A stopped example with a literal, non-success aggregate exit."""

    def __init__(self, reason: str, exit_code: int = 22) -> None:
        super().__init__(reason)
        self.exit_code = exit_code


def demand(condition: bool, reason: str) -> None:
    if not condition:
        raise Stop(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def components(path: Path) -> None:
    """Refuse observed symlinks and Windows reparse points, including ancestors."""
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        demand(
            not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "Symlink/reparse components are not supported.",
        )


def child(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    demand(
        bool(name)
        and not relative.is_absolute()
        and "\\" not in name
        and ":" not in name
        and all(part not in {"", ".", ".."} for part in name.split("/")),
        "Unsafe relative file name.",
    )
    path = root.joinpath(*relative.parts)
    components(path)
    return path


def read(root: Path, name: str, maximum: int = FILE_LIMIT) -> bytes:
    """Bounded regular-file read with descriptor and named identity checks."""
    path = child(root, name)
    observed = path.lstat()
    demand(
        stat.S_ISREG(observed.st_mode) and observed.st_size <= maximum,
        "Expected a bounded regular file.",
    )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    with os.fdopen(os.open(path, flags), "rb") as source:
        before = os.fstat(source.fileno())
        demand(
            stat.S_ISREG(before.st_mode) and before.st_size <= maximum,
            "Expected a bounded regular file.",
        )
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
        # Windows 3.12 path and descriptor APIs give ctime different meanings.
        # Reopen the name while holding the capture descriptor; compare like APIs.
        components(path)
        with os.fdopen(os.open(path, flags), "rb") as current:
            named = os.fstat(current.fileno())
    identities = {
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        for item in (before, after, named)
    }
    demand(len(raw) <= maximum and len(identities) == 1, "File changed during capture.")
    return raw


def write_new(root: Path, name: str, raw: bytes) -> None:
    path = child(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    components(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    demand(read(root, name, max(FILE_LIMIT, len(raw))) == raw, "Staged bytes changed.")


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def decode(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            demand(key not in result, "Duplicate record key.")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise Stop("Non-JSON numeric constant.")

    def finite(value: str) -> float:
        number = float(value)
        demand(math.isfinite(number), "Nonfinite record number.")
        return number

    try:
        return json.loads(
            raw, object_pairs_hook=unique, parse_constant=constant, parse_float=finite
        )
    except (ValueError, RecursionError) as exc:
        raise Stop("Malformed JSON evidence.") from exc


def object_value(value: Any, required: set[str]) -> dict[str, Any]:
    demand(type(value) is dict and required <= value.keys(), "Record fields missing or mistyped.")
    return value


def digest_entries(entries: list[dict[str, Any]]) -> str:
    rows = [[item["relative_path"], item["sha256"], item["size"], item["mode"]] for item in entries]
    return sha(json.dumps(rows, separators=(",", ":")).encode("utf-8"))


def identify(root: Path, files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": name,
            "sha256": sha(raw),
            "size": len(raw),
            "mode": stat.S_IMODE(child(root, name).stat().st_mode) & 0o777,
        }
        for name, raw in sorted(files.items())
    ]


def process_passed(value: Any) -> bool:
    process = object_value(value, {"returncode", "error", "stop_reason", "cleanup_confirmed"})
    return (
        type(process["returncode"]) is int
        and process["returncode"] == 0
        and process["error"] is None
        and process["stop_reason"] is None
        and process["cleanup_confirmed"] is True
    )


@dataclass(frozen=True)
class Admission:
    record_path: Path
    record_digest: str
    artifact_path: Path
    raw: bytes
    checks: list[dict[str, Any]]


def admit(
    caller: Path,
    output: str,
    command: str,
    expected_files: list[dict[str, Any]],
    exit_code: int,
    model: str | None,
) -> Admission:
    """Admit only complete passing UNPROVEN observations for this exact invocation."""
    demand(type(exit_code) is int and exit_code in (20, 21, 22), "Unexpected execution exit.")
    runs = child(caller, ".apm/runs")
    if not runs.exists():
        raise Stop("No run record was admitted; inspect apmx diagnostics.", exit_code)
    entries = list(runs.iterdir())
    demand(len(entries) == 1, "Expected one run in this fresh caller; no latest-run fallback.")
    run = entries[0]
    components(run)
    demand(
        run.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{12}", run.name) is not None,
        "Unexpected run directory.",
    )
    raw_record = read(run, "record.json", 2 * 1024 * 1024)
    record = object_value(
        decode(raw_record),
        {
            "schema",
            "profile",
            "complete",
            "phase",
            "run_id",
            "attempt_id",
            "caller_root",
            "evidence_root",
            "result",
        },
    )
    demand(
        record["schema"] == "apm-contract-run/0.1" and record["profile"] == "native-advisory",
        "Unsupported record schema/profile.",
    )
    demand(
        record["run_id"] == run.name
        and record["attempt_id"] == run.name + "/1"
        and record["caller_root"] == str(caller)
        and record["evidence_root"] == str(runs),
        "Run/caller identity mismatch.",
    )
    result = object_value(
        record["result"],
        {
            "run_id",
            "run_directory",
            "outcome",
            "artifact",
            "checks",
            "stop_reason",
        },
    )
    outcome = object_value(result["outcome"], {"name", "exit_code"})
    demand(
        type(outcome["exit_code"]) is int
        and outcome["exit_code"] == exit_code
        and outcome["name"] == {20: "REJECTED", 21: "UNPROVEN", 22: "HALTED"}[exit_code],
        "Process exit and recorded outcome disagree.",
    )
    demand(
        result["run_id"] == run.name and result["run_directory"] == str(run),
        "Result run identity mismatch.",
    )
    if exit_code == 22:
        raise Stop("apmx halted; inspect the exact stage record.")
    demand(
        record["complete"] is True and record["phase"] == "finished", "Run record is incomplete."
    )
    demand(result["stop_reason"] is None, "Stopped result cannot be admitted.")
    checks = result["checks"]
    demand(type(checks) is list, "Expected check observations.")
    if exit_code == 20:
        demand(
            any(
                type(item) is dict
                and type(item.get("normalized")) is int
                and item["normalized"] == 1
                for item in checks
            ),
            "REJECTED without a failed check.",
        )
        raise Stop("Independent check rejected the stage; no later stage ran.", 20)
    if result["artifact"] is None:
        demand(
            not checks and record.get("artifact") is None, "Missing-output record is inconsistent."
        )
        raise Stop("UNPROVEN: no captured output; chain incomplete.", 21)
    object_value(
        record,
        {
            "producer",
            "source",
            "baseline",
            "controls",
            "artifact",
            "checks",
            "requested_model",
            "native_reported_exit_code",
            "child_pid",
            "child_pgid",
            "active_check",
            "transcript",
        },
    )
    demand(
        record["checks"] == checks and record["artifact"] == result["artifact"],
        "Conflicting artifact/check observations.",
    )
    demand(
        process_passed(record["producer"])
        and record["child_pid"] is None
        and record["child_pgid"] is None
        and record["active_check"] is None,
        "Producer completion/cleanup is not established.",
    )
    native_exit = record["native_reported_exit_code"]
    demand(
        native_exit is None or (type(native_exit) is int and native_exit == 0),
        "Native reported failure.",
    )
    demand(record["requested_model"] == model, "Requested model differs from the invocation.")
    controls = object_value(record["controls"], {"isolation", "spend_cap"})
    demand(
        controls["isolation"] == "unavailable" and controls["spend_cap"] == "unavailable",
        "Unexpected native assurance controls.",
    )
    source = object_value(record["source"], {"path", "sha256", "retained", "retained_identities"})
    contract = next(item for item in expected_files if item["relative_path"] == "job.contract.md")
    demand(
        source["path"] == str(caller / "job.contract.md")
        and source["sha256"] == contract["sha256"],
        "Executed contract differs from the staged source.",
    )
    retained = object_value(source["retained"], {"contract.contract.md"})
    demand(
        retained["contract.contract.md"] == str(run / "source/contract.contract.md"),
        "Retained source location mismatch.",
    )
    demand(
        sha(read(run, "source/contract.contract.md")) == contract["sha256"],
        "Retained contract bytes changed.",
    )
    demand(
        source["retained_identities"]
        == [
            {
                "relative_path": "contract.contract.md",
                "sha256": contract["sha256"],
                "size": contract["size"],
                "mode": 0o400,
            },
        ],
        "Retained source identities differ from the direct contract.",
    )
    baseline = object_value(
        record["baseline"],
        {
            "root",
            "producer",
            "files",
            "digest",
            "resources_digest",
        },
    )
    demand(
        baseline["root"] == str(run / "baseline") and baseline["producer"] == str(run / "producer"),
        "Baseline location mismatch.",
    )
    demand(
        type(baseline["files"]) is list and baseline["files"] == expected_files,
        "Captured input/check identities differ from the host handoff.",
    )
    demand(baseline["digest"] == digest_entries(expected_files), "Baseline digest mismatch.")
    resources = [item for item in expected_files if item["relative_path"].startswith("checks/")]
    demand(
        baseline["resources_digest"] == digest_entries(resources), "Check resource digest mismatch."
    )
    for item in expected_files:
        for root in (caller, run / "baseline"):
            raw = read(root, item["relative_path"])
            demand(
                sha(raw) == item["sha256"] and len(raw) == item["size"],
                "Supplied source/input/check bytes changed.",
            )
    artifact = object_value(result["artifact"], {"relative_path", "path", "sha256", "size"})
    artifact_path = child(run, "artifacts/" + output)
    demand(
        artifact["relative_path"] == output and artifact["path"] == str(artifact_path),
        "Output is not this stage's retained artifact.",
    )
    captured = read(run, "artifacts/" + output)
    demand(
        type(artifact["size"]) is int
        and artifact["size"] == len(captured)
        and artifact["sha256"] == sha(captured),
        "Retained artifact size/digest mismatch.",
    )
    demand(len(checks) == 1, "Expected the exact nonempty check set.")
    check = object_value(
        checks[0],
        {
            "name",
            "command",
            "normalized",
            "process",
            "subject_digest",
            "resources_digest",
        },
    )
    demand(
        check["name"] == "contract" and check["command"] == command, "Unexpected check identity."
    )
    demand(
        check["subject_digest"] == artifact["sha256"]
        and check["resources_digest"] == baseline["resources_digest"],
        "Check did not assess the retained subject/resources.",
    )
    demand(
        type(check["normalized"]) is int and check["normalized"] in (0, 1, 2),
        "Invalid normalized check status.",
    )
    if check["normalized"] == 2:
        raise Stop("UNPROVEN: check incomplete; chain incomplete.", 21)
    demand(
        check["normalized"] == 0 and process_passed(check["process"]),
        "Passing check and process observations disagree.",
    )
    transcript = object_value(record["transcript"], {"relative_path", "sha256", "size"})
    demand(transcript["relative_path"] == "transcript.log", "Unexpected transcript name.")
    log = read(run, "transcript.log", 4 * 1024 * 1024)
    demand(
        type(transcript["size"]) is int
        and transcript["size"] == len(log)
        and transcript["sha256"] == sha(log),
        "Transcript identity mismatch.",
    )
    return Admission(
        run / "record.json",
        sha(raw_record),
        artifact_path,
        captured,
        [{"name": "contract", "normalized": 0, "raw_exit": 0}],
    )
