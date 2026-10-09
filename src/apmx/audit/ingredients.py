"""Ingredients row: APM, the canonical owner of dependency trust, audits retained inputs.

APMX does not reimplement dependency trust. The receipt retains the exact
``apm.yml`` and ``apm.lock.yaml`` used by the run. They are staged into a fresh
temporary project, and the bundled pinned APM runs ``apm install --frozen``
followed by ``apm audit --ci``. APMX then confirms that the reinstall did not
change any recorded content hash, that selected skill bodies match the
receipt, and that the pinned APM derives the receipt's CycloneDX components
from the retained lock. No receipt code is executed: APM runs with scripts
disabled and without bin/ deployment, and the temporary project is removed.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from apmx.contracts.models import ContractError, ContractLimits, ProcessRequest
from apmx.contracts.process import supervise_process
from apmx.contracts.stream import safe_text
from apmx.deps.lockfile import LockFile
from apmx.utils.yaml_io import load_yaml_str

from . import verifier

PASS, WARN, FAIL, UNAVAILABLE = "pass", "warn", "fail", "unavailable"
_LOCK_NAMES = ("apm.lock.yaml", "apm.lock")
# `apm audit --ci` checks that run without any policy (APM 0.30.0). Any other check
# name proves that a policy was actually loaded and enforced.
_BASELINE_CHECKS = frozenset(
    {
        "lockfile-exists",
        "ref-consistency",
        "deployment-ledger-owners",
        "deployed-files-present",
        "no-orphaned-packages",
        "skill-subset-consistency",
        "config-consistency",
        "content-integrity",
        "includes-consent",
        "drift",
    }
)
_OUTPUT_BYTES = 8 * 1024 * 1024
_DIAGNOSTIC_BYTES = 64 * 1024


@dataclass
class Ingredients:
    status: str
    detail: str
    notes: list[str] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)


def _selected_source(receipt: verifier.Receipt) -> str:
    runs = [item["runId"] for item in receipt.index["attempts"] if item["selected"]]
    return f"attempts/{runs[0]}/source"


def _retained(receipt: verifier.Receipt, name: str) -> bytes | None:
    path = f"{_selected_source(receipt)}/{name}"
    return verifier.read(receipt.root, path) if path in receipt.index["files"] else None


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _dependencies(count: int) -> str:
    return f"{count} APM {'dependency' if count == 1 else 'dependencies'}"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class _Bounded:
    def __init__(self) -> None:
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.overflow = False

    def __call__(self, stream: str, chunk: bytes) -> None:
        if stream == "stdout":
            if len(self.stdout) + len(chunk) > _OUTPUT_BYTES:
                self.overflow = True
            else:
                self.stdout.extend(chunk)
        else:
            self.stderr.extend(chunk)
            del self.stderr[:-_DIAGNOSTIC_BYTES]


def _policy_diagnostic(capture: _Bounded) -> str:
    for line in bytes(capture.stderr).decode("utf-8", errors="replace").splitlines():
        if "policy" in line.lower() or "enforcement skipped" in line.lower():
            text = line.strip().removeprefix("[!]").strip().split(" (set policy.", 1)[0]
            text = safe_text(text.removesuffix("; enforcement skipped"), limit=200)
            return text.encode("ascii", errors="replace").decode("ascii")
    return "APM reported no policy checks"


def _last_line(capture: _Bounded) -> str:
    lines = [
        line.strip()
        for line in (bytes(capture.stderr) + b"\n" + bytes(capture.stdout))
        .decode("utf-8", errors="replace")
        .splitlines()
        if line.strip()
    ]
    if not lines:
        return "no diagnostic output"
    return safe_text(lines[-1], limit=200).encode("ascii", errors="replace").decode("ascii")


def _run(
    argv: list[str], cwd: Path, env: Mapping[str, str], limits: ContractLimits
) -> tuple[bool, int | None, _Bounded]:
    capture = _Bounded()
    observed = supervise_process(
        ProcessRequest(tuple(argv), cwd, limits.attempt_seconds, env=env),
        on_bytes=capture,
        limits=limits,
    )
    finished = (
        not observed.stop_reason
        and not observed.error
        and observed.cleanup_confirmed
        and not capture.overflow
    )
    return finished, observed.returncode, capture


def _hashes(lock_raw: bytes) -> dict[str, tuple]:
    """Content identities APM records per dependency; other lock fields may legitimately vary."""
    data = load_yaml_str(lock_raw.decode("utf-8")) or {}
    result = {}
    for entry in data.get("dependencies") or []:
        key = entry.get("local_path") or entry.get("repo_url") or entry.get("name")
        result[str(key)] = (
            entry.get("resolved_commit"),
            entry.get("content_hash"),
            entry.get("resolved_hash"),
            tuple(sorted((entry.get("deployed_file_hashes") or {}).items())),
        )
    return result


def _components(raw: bytes) -> list[str]:
    bom = json.loads(raw)
    return sorted(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in bom.get("components", [])
    )


def resolve_policy(policy: str | None, cwd: Path) -> str | None:
    """Local policy files are resolved before APM runs in the temporary project."""
    if policy is None:
        return None
    candidate = Path(policy).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    if candidate.is_file():
        return str(candidate.resolve())
    return policy


def audit_ingredients(
    receipt: verifier.Receipt,
    *,
    policy: str | None,
    offline: bool,
    limits: ContractLimits | None = None,
) -> Ingredients:
    """Delegate dependency trust to the bundled pinned APM; never pass silently."""
    from apmx.contracts.evidence import lock_dependencies
    from apmx.install import apm_backend

    limits = limits or ContractLimits()
    recorded = receipt.definition.get("resolvedDependencies", [])
    lock_name = next((name for name in _LOCK_NAMES if _retained(receipt, name) is not None), None)
    if lock_name is None:
        if recorded:
            return Ingredients(
                FAIL,
                f"receipt lists {_dependencies(len(recorded))} but retains no APM lock",
            )
        if receipt.bom.get("components"):
            return Ingredients(FAIL, "CycloneDX lists components but no APM lock was retained")
        return Ingredients(
            PASS, "0 APM dependencies   nothing to audit", facts={"apmDependencies": 0}
        )
    lock_raw = _retained(receipt, lock_name)
    manifest = _retained(receipt, "imports-apm.yml") or _retained(receipt, "apm.yml")
    inventory_manifest = _retained(receipt, "apm.yml")
    if manifest is None or inventory_manifest is None:
        return Ingredients(FAIL, "the retained APM lock has no retained apm.yml")
    try:
        lock = LockFile.from_yaml(lock_raw.decode("utf-8"))
        projected = lock_dependencies(lock)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return Ingredients(FAIL, "the retained APM lock is malformed")
    if projected != recorded:
        return Ingredients(FAIL, "the retained APM lock disagrees with the factory definition")
    count = len(recorded)
    facts: dict[str, object] = {"apmDependencies": count}
    if count == 0:
        return Ingredients(PASS, "0 APM dependencies   nothing to audit", facts=facts)
    label = _dependencies(count)
    if offline:
        return Ingredients(
            WARN,
            f"not audited (offline): {label}; retained lock matches the definition",
            facts={**facts, "apmAudit": "skipped-offline"},
        )
    try:
        executable = apm_backend.locate_backend()
        identity = apm_backend.backend_identity(executable)
    except ContractError as exc:
        return Ingredients(UNAVAILABLE, f"bundled APM unavailable: {exc}", facts=facts)
    facts["apmVersion"] = identity["version"]
    env = apm_backend.backend_child_env(dict(os.environ))
    for name in ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS"):
        env.pop(name, None)
    env.update(NO_COLOR="1", TERM="dumb", COLUMNS="4096")
    notes: list[str] = []
    try:
        with TemporaryDirectory(prefix="apmx-audit-") as temporary:
            stage = Path(temporary).resolve()
            (stage / "apm.yml").write_bytes(manifest)
            (stage / lock_name).write_bytes(lock_raw)
            apm_backend._check_backend_version(executable, stage, env, limits)
            finished, code, capture = _run(
                [
                    str(executable),
                    "install",
                    "--frozen",
                    "--root",
                    str(stage),
                    "--only",
                    "apm",
                    "--target",
                    "agent-skills",
                    "--no-trust-bin",
                    "--no-policy",
                ],
                stage,
                env,
                limits,
            )
            if not finished or code != 0:
                return Ingredients(
                    UNAVAILABLE,
                    f"{label}: apm install --frozen could not reinstall them "
                    f"({_last_line(capture)})",
                    [
                        (
                            "Check network, authentication and package access; "
                            "use --offline to audit everything else."
                        )
                    ],
                    facts={**facts, "apmInstall": "failed"},
                )
            reinstalled = (stage / lock_name).read_bytes()
            before, after = _hashes(lock_raw), _hashes(reinstalled)
            drifted = sorted(key for key in before if after.get(key) != before[key])
            (stage / lock_name).write_bytes(lock_raw)
            if drifted:
                name = Path(drifted[0]).name or drifted[0]
                return Ingredients(
                    FAIL,
                    f"{label}: reinstalled content differs from the receipt ({name})",
                    facts={**facts, "drifted": [Path(item).name for item in drifted]},
                )
            for binding in receipt.bindings:
                body = stage / ".agents/skills" / binding["capability"] / "SKILL.md"
                if not body.is_file() or _sha(body.read_bytes()) != binding["bodySha256"]:
                    return Ingredients(
                        FAIL,
                        f"{label}: reinstalled skill {binding['capability']} does not "
                        "match the receipt's bodySha256",
                        facts=facts,
                    )
            exported = apm_backend.export_cyclonedx(
                inventory_manifest, lock_raw, expected_backend=identity, limits=limits
            )
            if _components(exported) != _components(
                verifier.descriptor(receipt.root, receipt.index["inventory"])
            ):
                return Ingredients(
                    FAIL,
                    f"{label}: CycloneDX components differ from what the pinned APM "
                    "derives from the retained lock",
                    facts=facts,
                )
            audit = [str(executable), "audit", "--ci", "--no-fail-fast", "-f", "json"]
            audit += ["--policy", policy] if policy else ["--no-policy"]
            finished, code, capture = _run(audit, stage, env, limits)
            try:
                report = json.loads(bytes(capture.stdout))
                passed = report["passed"]
                checks = report["checks"]
            except (ValueError, KeyError, TypeError):
                report = None
            if not finished or report is None or code not in (0, 1):
                return Ingredients(
                    UNAVAILABLE,
                    f"{label}: apm audit --ci did not produce a result ({_last_line(capture)})",
                    facts={**facts, "apmAudit": "unavailable"},
                )
            names = {str(item.get("name")) for item in checks if isinstance(item, dict)}
            if policy and not names - _BASELINE_CHECKS:
                return Ingredients(
                    UNAVAILABLE,
                    f"{label}: policy {safe_text(policy, limit=120)} was not enforced "
                    f"({_policy_diagnostic(capture)})",
                    [
                        (
                            "A policy that cannot be loaded is never treated as a pass. "
                            "'org' needs a git remote, which the temporary audit project "
                            "lacks; pass owner/repo, a URL or a file instead."
                        )
                    ],
                    facts={**facts, "apmAudit": "policy-not-enforced"},
                )
            failing = [
                safe_text(str(item.get("name")), limit=80)
                for item in checks
                if isinstance(item, dict) and not item.get("passed")
            ]
            facts["apmAudit"] = {
                "passed": bool(passed) and code == 0,
                "policy": policy or "none (--no-policy)",
                "checks": len(checks),
                "failing": failing,
            }
            if code != 0 or not passed or failing:
                for item in checks:
                    if isinstance(item, dict) and not item.get("passed"):
                        notes.append(
                            f"{safe_text(str(item.get('name')), limit=80)}: "
                            f"{safe_text(str(item.get('message')), limit=160)}"
                        )
                return Ingredients(
                    FAIL,
                    f"{label}   apm audit --ci{' --policy' if policy else ''}: "
                    f"{_plural(len(failing) or 1, 'failing check')} "
                    f"({', '.join(failing) or 'unreported'})",
                    notes,
                    facts=facts,
                )
            notes.append("installed bytes match CycloneDX inventory and recorded hashes")
            scope = "policy, content, lock, drift" if policy else "content, lock, drift"
            return Ingredients(
                PASS, f"{label}   apm audit --ci: clean ({scope})", notes, facts=facts
            )
    except ContractError as exc:
        return Ingredients(UNAVAILABLE, f"{label}: {exc}", facts=facts)
    except OSError:
        return Ingredients(
            UNAVAILABLE, f"{label}: the temporary audit project could not be used", facts=facts
        )
