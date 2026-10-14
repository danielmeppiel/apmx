"""Policy for ``apmx audit``: the same ``apm-policy.yml``, an apmx-owned section.

There is one governance file, ``apm-policy.yml``, found the way APM finds it:

1. ``--policy SOURCE`` (an override: ``org``, ``owner/repo``, ``https://`` or a file).
2. The repository's ``apm-policy.yml`` at the Git top level. In a pull request
   it is read from the PR **base** commit, so a PR cannot loosen its own gate.
3. The organization policy repository APM discovers from the ``origin`` remote
   (``.github-private``, ``.github``, ``.apm``, ``_apm``), following ``extends:``.

APM owns its own keys and enforces them in the Ingredients row (``apm audit
--ci --policy``). apmx owns exactly one top-level section, ``factories``, and
reads it from the raw documents itself; it never reads or reinterprets APM's
keys (except top-level ``enforcement: off`` and ``extends``). The bundled APM
is reused only to locate the policy and to say whether it loads
(``apm policy status --json``). APM 0.33.0 warns that ``factories`` is an
unknown top-level key; that is expected and harmless. It also reports a policy
whose only section is ``factories`` as ``empty``, so ``empty`` never means
"no policy" here when a ``factories`` section exists.

``factories`` rules are gate rules: a violation is exit 1 unless
``factories.enforcement: warn`` stages a rollout. APM's top-level
``enforcement: warn`` does not soften them; only ``enforcement: off`` disables
them, and the audit says so.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

POLICY_FILE = "apm-policy.yml"
FACTORY_ENFORCEMENT = ("warn", "block")
_TOP_ENFORCEMENT = ("off", "warn", "block")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ALLOW = re.compile(r"(?:(?P<name>[^@\s]+)@)?sha256:(?P<digest>[0-9a-f]{64})|(?P<pattern>[^@\s]+)")
_WORKFLOW = re.compile(
    r"(?P<workflow>(?:[A-Za-z0-9.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/"
    r"[A-Za-z0-9_.-]+\.ya?ml)(?:@(?P<ref>[^\s@]+))?"
)
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_FACTORY_KEYS = {"allow", "require_signed", "signers", "revoked", "enforcement"}
_LOADED = {"found", "empty"}
_ABSENT = {"absent", "no_git_remote"}
_RAW_BYTES = 1024 * 1024
_MAX_CHAIN = 5


class PolicyError(ValueError):
    """A policy that exists but cannot be used: never a pass."""


@dataclass(frozen=True)
class Signer:
    """A workflow allowed to sign receipts, optionally pinned to a ref (``x.yml@refs/tags/v1``)."""

    workflow: str
    ref: str | None = None


@dataclass(frozen=True)
class Revoked:
    """Capability content revoked by incident response: exact ``purl`` and ``bodySha256``."""

    purl: str
    body_sha256: str


@dataclass(frozen=True)
class FactoryPolicy:
    """The merged ``factories`` section of one resolved policy chain."""

    source: str
    enforcement: str = "block"
    allow: tuple[tuple[str | None, str], ...] | None = None
    unpinned: tuple[str, ...] = ()
    require_signed: bool = False
    signers: tuple[Signer, ...] | None = None
    revoked: tuple[Revoked, ...] = ()
    present: bool = False

    @property
    def rules(self) -> bool:
        return bool(self.allow is not None or self.require_signed or self.signers or self.revoked)


@dataclass
class Resolution:
    """Outcome of discovery: ``none``, ``found``, ``partial`` (offline) or ``unloadable``."""

    status: str
    detail: str
    policy: FactoryPolicy | None = None
    apm_source: str | None = None
    origin: str | None = None
    notes: list[str] = field(default_factory=list)
    facts: dict[str, object] = field(default_factory=dict)
    _temporary: tempfile.TemporaryDirectory | None = None

    @property
    def applies(self) -> bool:
        return self.policy is not None and self.policy.enforcement != "off"

    def cleanup(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


# --------------------------------------------------------------------------- parsing


def document_keys(text: str, label: str) -> dict:
    """Read apmx's ``factories`` section, plus ``extends`` and ``enforcement``, from raw YAML.

    A typo such as ``require_sigend`` must never silently drop a requirement,
    so unknown keys inside ``factories`` and malformed digests make the whole
    policy unusable (fail closed). APM's own keys are never read.
    """
    from apmx.utils.yaml_io import load_yaml_str

    try:
        data = load_yaml_str(text)
    except Exception as exc:
        raise PolicyError(f"{label}: not valid YAML ({type(exc).__name__})") from exc
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise PolicyError(f"{label}: a policy must be a YAML mapping")
    result: dict[str, object] = {}
    top = data.get("enforcement")
    if top is False:  # YAML 1.1 reads a bare `off` as false
        top = "off"
    if top in _TOP_ENFORCEMENT:
        result["top_enforcement"] = top
    extends = data.get("extends")
    if extends is not None:
        if not isinstance(extends, str) or not extends.strip():
            raise PolicyError(f"{label}: extends must be a string")
        result["extends"] = extends.strip()
    if data.get("factories") is not None:
        result.update(factories_section(data["factories"], label))
    return result


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PolicyError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def factories_section(value: object, label: str) -> dict:
    """Validate one ``factories`` mapping; ``null`` lists mean no opinion, ``[]`` means none."""
    if not isinstance(value, dict):
        raise PolicyError(f"{label}: factories must be a mapping")
    unknown = sorted(set(map(str, value)) - _FACTORY_KEYS)
    if unknown:
        raise PolicyError(
            f"{label}: unknown key factories.{unknown[0]} "
            "(expected allow, require_signed, signers, revoked, enforcement)"
        )
    result: dict[str, object] = {"present": True}
    enforcement = value.get("enforcement")
    if enforcement is not None:
        if enforcement not in FACTORY_ENFORCEMENT:
            raise PolicyError(f"{label}: factories.enforcement must be block or warn")
        result["enforcement"] = enforcement
    if value.get("allow") is not None:
        entries, unpinned = [], []
        for item in _strings(value["allow"], f"{label}: factories.allow"):
            match = _ALLOW.fullmatch(item)
            if match is None or (match.group("pattern") and "sha256:" in item):
                raise PolicyError(
                    f"{label}: factories.allow entry {item!r} must be "
                    "<package pattern>[@sha256:<definition digest, 64 lowercase hex>]"
                )
            if match.group("pattern"):
                unpinned.append(match.group("pattern"))
            else:
                entries.append((match.group("name"), match.group("digest")))
        result["allow"] = tuple(entries)
        result["unpinned"] = tuple(unpinned)
    if "require_signed" in value:
        if not isinstance(value["require_signed"], bool):
            raise PolicyError(f"{label}: factories.require_signed must be true or false")
        result["require_signed"] = value["require_signed"]
    if value.get("signers") is not None:
        if not isinstance(value["signers"], list):
            raise PolicyError(f"{label}: factories.signers must be a list")
        signers = []
        for index, item in enumerate(value["signers"]):
            where = f"{label}: factories.signers[{index}]"
            if not isinstance(item, dict) or set(map(str, item)) != {"workflow"}:
                raise PolicyError(f"{where} must be a mapping with only workflow")
            match = _WORKFLOW.fullmatch(str(item["workflow"]))
            if not isinstance(item["workflow"], str) or match is None:
                raise PolicyError(
                    f"{where}.workflow must be owner/repo/.github/workflows/<file>.yml[@<ref>]"
                )
            signers.append(Signer(match.group("workflow"), match.group("ref")))
        result["signers"] = tuple(signers)
    if value.get("revoked") is not None:
        if not isinstance(value["revoked"], list):
            raise PolicyError(f"{label}: factories.revoked must be a list")
        revoked = []
        for index, item in enumerate(value["revoked"]):
            where = f"{label}: factories.revoked[{index}]"
            if not isinstance(item, dict) or set(map(str, item)) != {"purl", "bodySha256"}:
                raise PolicyError(f"{where} must be a mapping of exactly purl and bodySha256")
            purl, body = item["purl"], item["bodySha256"]
            if not isinstance(purl, str) or not purl.startswith("pkg:"):
                raise PolicyError(f"{where}.purl must be a package URL (pkg:...)")
            # fullmatch, not match with "$": "$" also matches before a trailing newline.
            if not isinstance(body, str) or not _SHA256.fullmatch(body):
                raise PolicyError(f"{where}.bodySha256 must be 64 lowercase hex characters")
            revoked.append(Revoked(purl, body))
        result["revoked"] = tuple(revoked)
    return result


def merge(documents: Iterable[dict], *, source: str) -> FactoryPolicy:
    """Merge one ``extends`` chain tighten-only; order-independent.

    ``allow`` and ``signers`` intersect across the levels that set them,
    ``require_signed`` ORs, ``revoked`` unions, and ``factories.enforcement``
    is ``block`` unless every level that sets it says ``warn``. Top-level
    ``enforcement: off`` (APM's switch) disables the rules only when no level
    sets a stricter top-level enforcement.
    """
    allow: dict[str, str | None] | None = None
    unpinned: list[str] = []
    signers: set[Signer] | None = None
    revoked: list[Revoked] = []
    require_signed = present = False
    levels: list[str] = []
    top: list[str] = []
    for document in documents:
        present = present or bool(document.get("present"))
        if "top_enforcement" in document:
            top.append(str(document["top_enforcement"]))
        if document.get("present"):
            levels.append(str(document.get("enforcement", "block")))
        if "allow" in document:
            entries = {digest: name for name, digest in document["allow"]}
            allow = (
                entries
                if allow is None
                else {d: allow[d] or name for d, name in entries.items() if d in allow}
            )
            unpinned += [p for p in document.get("unpinned", ()) if p not in unpinned]
        if "signers" in document:
            listed = set(document["signers"])
            signers = listed if signers is None else signers & listed
        require_signed = require_signed or bool(document.get("require_signed"))
        revoked += [r for r in document.get("revoked", ()) if r not in revoked]
    enforcement = "block" if not levels or "block" in levels else "warn"
    if top and max(top, key=_TOP_ENFORCEMENT.index) == "off":
        enforcement = "off"
    return FactoryPolicy(
        source=source,
        enforcement=enforcement,
        allow=None if allow is None else tuple((name, d) for d, name in sorted(allow.items())),
        unpinned=tuple(unpinned),
        require_signed=require_signed,
        signers=None
        if signers is None
        else tuple(sorted(signers, key=lambda s: (s.workflow, s.ref or ""))),
        revoked=tuple(revoked),
        present=present,
    )


# --------------------------------------------------------------------------- discovery


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.decode("utf-8", errors="replace") if completed.returncode == 0 else None


def repository_root(cwd: Path) -> Path:
    top = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(top.strip()) if top and top.strip() else cwd


def origin_remote(root: Path) -> str | None:
    url = _git(root, "remote", "get-url", "origin")
    return url.strip() or None if url else None


def _apm_status(root: Path, source: str | None) -> dict:
    """Ask the bundled APM where the policy is and whether APM can load it."""
    from apmx.contracts.models import ContractError, ContractLimits
    from apmx.install import apm_backend

    from .ingredients import _last_line, _run

    try:
        executable = apm_backend.locate_backend()
    except ContractError as exc:
        raise PolicyError(f"bundled APM unavailable: {exc}") from exc
    env = apm_backend.backend_child_env(dict(os.environ))
    env.update(NO_COLOR="1", TERM="dumb", COLUMNS="4096")
    argv = [str(executable), "policy", "status", "--json"]
    if source is not None:
        argv += ["--policy-source", source]
    finished, code, capture = _run(argv, root, env, ContractLimits())
    try:
        report = json.loads(bytes(capture.stdout))
    except ValueError:
        report = None
    if not finished or code != 0 or not isinstance(report, dict):
        raise PolicyError(f"apm policy status did not produce a result ({_last_line(capture)})")
    return report


def _read_raw(ref: str, root: Path) -> str:
    """Fetch the raw bytes of one policy document (files, https URLs, github.com repos)."""
    for prefix in ("file:", "url:", "org:", "repo:"):
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
            break
    path = Path(ref).expanduser()
    candidate = path if path.is_absolute() else root / path
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    if ref.startswith("https://"):
        url, headers = ref, {}
    else:
        parts = [part for part in ref.split("/") if part]
        if len(parts) == 3 and parts[0] == "github.com":
            parts = parts[1:]
        if len(parts) != 2 or not _REPO.fullmatch("/".join(parts)):
            raise PolicyError(
                f"apmx cannot read factories from policy {ref!r} yet "
                "(supported: files, https:// URLs and github.com owner/repo)"
            )
        from apmx.core.token_manager import GitHubTokenManager

        url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}/contents/{POLICY_FILE}"
        headers = {"Accept": "application/vnd.github.raw", "X-GitHub-Api-Version": "2022-11-28"}
        token = GitHubTokenManager().get_token_for_purpose("modules")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers={"User-Agent": "apmx-audit", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(_RAW_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise PolicyError(f"could not read policy {ref} ({exc})") from exc
    if len(raw) > _RAW_BYTES:
        raise PolicyError(f"policy {ref} is larger than 1 MiB")
    return raw.decode("utf-8", errors="strict")


class PolicyReader:
    """Discovery seams: ``status`` asks the bundled APM, ``read`` fetches raw bytes.

    Both are injectable, so every default and fail-closed case is tested
    without network or APM.
    """

    def __init__(
        self,
        status: Callable[[Path, str | None], dict] | None = None,
        read: Callable[[str, Path], str] | None = None,
    ) -> None:
        self.status = status if status is not None else _apm_status
        self.read = read if read is not None else _read_raw


def _label(source: str, root: Path) -> str:
    text = source.removeprefix("file:")
    try:
        return str(Path(text).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return source


def resolve(
    cwd: Path,
    *,
    override: str | None = None,
    offline: bool = False,
    base: str | None = None,
    reader: PolicyReader | None = None,
) -> Resolution:
    """Discover the policy for this audit. Never raises for policy problems."""
    reader = reader or PolicyReader()
    root = repository_root(cwd)
    temporary = None
    origin = None
    if override is not None:
        candidate = Path(override).expanduser()
        candidate = candidate if candidate.is_absolute() else cwd / candidate
        if candidate.is_file():
            source, kind, label = str(candidate.resolve()), "file", f"{override} (--policy)"
        else:
            source, kind, label = override, "remote", f"{override} (--policy)"
    elif base is not None and _git(root, "cat-file", "-e", f"{base}:{POLICY_FILE}") is not None:
        temporary = tempfile.TemporaryDirectory(prefix="apmx-policy-")
        path = Path(temporary.name) / POLICY_FILE
        path.write_text(_git(root, "show", f"{base}:{POLICY_FILE}") or "", encoding="utf-8")
        source, kind = str(path), "file"
        label = f"{POLICY_FILE} (repository, PR base {base[:12]})"
    elif (root / POLICY_FILE).is_file():
        source = str((root / POLICY_FILE).resolve())
        kind, label = "file", f"{POLICY_FILE} (repository)"
    else:
        origin = origin_remote(root)
        if origin is None:
            return Resolution(
                "none",
                "none found",
                notes=[f"no {POLICY_FILE} in {root.name or root} and no git origin for org policy"],
                facts={"policy": None},
            )
        if offline:
            return Resolution(
                "partial",
                "org policy not checked (offline)",
                notes=["--offline does not contact the network; org policy rules were not applied"],
                facts={"policy": "unchecked-offline"},
            )
        source, kind, label = None, "org", "org policy"
    resolution = _resolve(reader, root, source, kind, label, offline, override is not None)
    resolution._temporary = temporary
    resolution.origin = origin or origin_remote(root)
    if base is not None and override is None:
        resolution.facts["policyRef"] = base
    return resolution


def _chain(reader: PolicyReader, root: Path, leaf: str) -> tuple[list[dict], list[str]]:
    """Read the leaf and every ``extends`` parent; ``extends: org`` is located by APM."""
    documents: list[dict] = []
    chain: list[str] = []
    ref: str | None = leaf
    while ref is not None:
        if len(chain) >= _MAX_CHAIN or ref in chain:
            raise PolicyError("extends chain is cyclic or deeper than 5 policies")
        chain.append(ref)
        document = document_keys(reader.read(ref, root), _label(ref, root))
        documents.append(document)
        ref = document.get("extends")
        if ref == "org":
            parent = reader.status(root, None)
            if str(parent.get("outcome")) not in _LOADED or not parent.get("source"):
                raise PolicyError("extends org: APM could not locate the org policy")
            ref = str(parent["source"])
    return documents, chain


def _resolve(
    reader: PolicyReader,
    root: Path,
    source: str | None,
    kind: str,
    label: str,
    offline: bool,
    explicit: bool,
) -> Resolution:
    if offline:
        if kind != "file":
            raise PolicyError("--offline can only read a local policy file")
        try:
            document = document_keys(reader.read(source, root), label)
        except (PolicyError, OSError, UnicodeDecodeError) as exc:
            return _unloadable(label, exc)
        resolution = _found(merge([document], source=label), label, None, [label])
        if "extends" in document:
            resolution.status = "partial"
            resolution.notes.append(
                f"extends {document['extends']} not resolved (offline); its rules were not applied"
            )
        return resolution
    try:
        report = reader.status(root, source)
    except PolicyError as exc:
        return _unloadable(label, exc)
    outcome = str(report.get("outcome"))
    if outcome == "disabled" or (outcome in _ABSENT and kind == "org"):
        return Resolution(
            "none",
            "none found",
            notes=[f"APM reports {outcome}: no {POLICY_FILE} applies"],
            facts={"policy": None},
        )
    if outcome not in _LOADED:
        reason = report.get("error") or report.get("fetch_error") or outcome
        if outcome in _ABSENT and explicit:
            reason = f"policy {label} not found"
        return _unloadable(label, PolicyError(f"APM: {reason}"))
    resolved = str(report.get("source") or source or "")
    if kind == "org":
        label = f"{_label(resolved, root)} (org)"
    try:
        documents, chain = _chain(reader, root, source if kind == "file" else resolved or source)
    except (PolicyError, OSError, UnicodeDecodeError) as exc:
        return _unloadable(label, exc)
    policy = merge(documents, source=label)
    if outcome == "empty" and not policy.present:
        return Resolution(
            "none",
            "none found",
            notes=[f"{label} has no APM rules and no factories section"],
            facts={"policy": None},
        )
    # APM rules exist only when APM says "found"; an empty (factories-only) policy is
    # never passed to `apm audit --policy`, which would rightly report it as unenforced.
    apm_source = (source if kind == "file" else (source or "org")) if outcome == "found" else None
    resolution = _found(policy, label, apm_source, [_label(ref, root) for ref in chain])
    if len(chain) > 1:
        resolution.notes.append("extends " + " <- ".join(_label(r, root) for r in chain))
    return resolution


def _found(
    policy: FactoryPolicy, label: str, apm_source: str | None, chain: list[str]
) -> Resolution:
    if policy.enforcement == "off":
        detail, notes = (
            f"{label}   factories off",
            ["top-level enforcement: off disables factories rules"],
        )
    elif policy.present:
        detail, notes = f"{label}   factories enforcement {policy.enforcement}", []
    else:
        detail, notes = f"{label}   no factories section", []
    return Resolution(
        "found",
        detail,
        policy=policy,
        apm_source=apm_source,
        notes=notes,
        facts={
            "policy": {
                "source": label,
                "factories": policy.present,
                "enforcement": policy.enforcement,
                "chain": chain,
            }
        },
    )


def _unloadable(label: str, exc: Exception) -> Resolution:
    from apmx.contracts.stream import safe_text

    text = safe_text(str(exc), limit=200).encode("ascii", errors="replace").decode("ascii")
    return Resolution(
        "unloadable",
        f"{label} could not be loaded: {text}",
        notes=["A policy that cannot be loaded is never treated as a pass."],
        facts={"policy": {"source": label, "error": text}},
    )


# --------------------------------------------------------------------------- evaluation


@dataclass(frozen=True)
class Verdict:
    status: str  # pass | fail | warn | info
    detail: str
    notes: tuple[str, ...] = ()


def _violation(policy: FactoryPolicy, detail: str, notes: tuple[str, ...] = ()) -> Verdict:
    return Verdict("fail" if policy.enforcement == "block" else "warn", detail, notes)


def evaluate_factory(policy: FactoryPolicy, definition_sha256: str, base: str) -> Verdict:
    """The audited definition digest must be on ``factories.allow`` (if the policy has one)."""
    if policy.allow is None:
        return Verdict("pass", base)
    for name, digest in policy.allow:
        if digest == definition_sha256:
            label = f" ({name})" if name else ""
            return Verdict("pass", f"definition sha256:{digest[:12]}.. on factories.allow{label}")
    notes = [f"policy: {policy.source}; {len(policy.allow)} pinned definition(s)"]
    if policy.unpinned:
        notes.append(
            f"{len(policy.unpinned)} unpinned package pattern(s) allow nothing yet: receipts do "
            "not record their factory package; pin <package>@sha256:<definition digest>"
        )
    return _violation(
        policy,
        f"definition sha256:{definition_sha256[:12]}.. is not on factories.allow",
        tuple(notes),
    )


def evaluate_revocation(policy: FactoryPolicy, bindings: list[dict]) -> Verdict:
    """No selected capability may match a ``factories.revoked`` (purl, bodySha256) pair.

    Both fields must match exactly, so the same content stays revoked when it
    is reinstalled from another checkout path, and a purl alone never revokes.
    """
    revoked = {(item.purl, item.body_sha256) for item in policy.revoked}
    for binding in bindings:
        if (binding.get("purl"), binding.get("bodySha256")) in revoked:
            return _violation(
                policy,
                f"capability {binding.get('capability')} is revoked (factories.revoked)",
                (f"purl={binding.get('purl')}  bodySha256={binding.get('bodySha256')}",),
            )
    count = len(bindings)
    detail = f"0 of {count} capabilit{'y' if count == 1 else 'ies'} revoked"
    if not revoked:
        return Verdict("pass", f"{detail}; factories.revoked is empty")
    return Verdict(
        "pass",
        f"{detail} ({len(revoked)} factories.revoked entr{'y' if len(revoked) == 1 else 'ies'})",
    )


def evaluate_unsigned(
    policy: FactoryPolicy, detail: str = "unsigned, but factories.require_signed is set"
) -> Verdict | None:
    """An unsigned receipt against ``factories.require_signed``."""
    if not policy.require_signed:
        return None
    return _violation(policy, detail)
