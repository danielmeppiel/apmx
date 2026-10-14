"""Policy for ``apmx audit``: discovered like ``apm audit``, owned by APM.

There is one governance file: ``apm-policy.yml``. ``apmx audit`` discovers it
in the same order as ``apm audit``:

1. ``--policy SOURCE`` (an override: ``org``, ``owner/repo``, ``https://`` or a file).
2. The repository's ``apm-policy.yml`` at the Git top level. In a pull request
   it is read from the PR **base** commit, so a PR cannot loosen its own gate.
   It may ``extends: org``.
3. The organization policy APM discovers from the ``origin`` remote.

The bundled APM decides whether that policy loads and resolves its
``extends`` chain (``apm policy status --json``); apmx never accepts a policy
APM could not load. A missing policy is reported, never treated as a pass.

**Adapter seam (temporary).** APM 0.33.0 does not yet define the ``factories``
section or ``security.integrity.deny_hashes`` (it warns on unknown top-level
keys). Until a bundled APM returns them in its resolved policy,
:func:`extension_keys` reads only these apmx keys, plus ``dependencies.deny``,
``enforcement`` and ``extends``, from the exact files APM resolved, and
:func:`merge` combines them tighten-only. Replace :class:`ExtensionReader`
with APM's merged policy when it ships; the evaluation functions below take
only a :class:`FactoryPolicy` and audited facts, so they stay unchanged.
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
ENFORCEMENTS = ("off", "warn", "block")
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
_ALLOW = re.compile(r"(?:(?P<name>[^@\s]+)@)?sha256:(?P<digest>[0-9a-f]{64})|(?P<pattern>[^@\s]+)")
_WORKFLOW = re.compile(
    r"(?P<workflow>(?:[A-Za-z0-9.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/"
    r"[A-Za-z0-9_.-]+\.ya?ml)(?:@(?P<ref>[^\s@]+))?"
)
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_FACTORY_KEYS = {"allow", "require_signed", "signers"}
_SIGNER_KEYS = {"workflow"}
_LOADED = {"found"}
_ABSENT = {"absent", "no_git_remote", "empty", "disabled"}
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
class FactoryPolicy:
    """The apmx-relevant view of one resolved policy."""

    source: str
    enforcement: str = "warn"
    allow: tuple[tuple[str | None, str], ...] | None = None
    unpinned: tuple[str, ...] = ()
    require_signed: bool = False
    signers: tuple[Signer, ...] | None = None
    deny_hashes: tuple[str, ...] = ()
    deny_dependencies: tuple[str, ...] = ()

    @property
    def rules(self) -> bool:
        return bool(
            self.allow is not None
            or self.require_signed
            or self.signers
            or self.deny_hashes
            or self.deny_dependencies
        )


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


def extension_keys(text: str, label: str) -> dict:
    """Read only apmx's keys from one policy document; reject anything ambiguous.

    A typo such as ``require_sigend`` must never silently drop a requirement,
    so unknown keys inside ``factories`` and malformed digests make the whole
    policy unusable (fail closed).
    """
    from apmx.utils.yaml_io import load_yaml_str

    try:
        data = load_yaml_str(text)
    except Exception as exc:
        raise PolicyError(f"{label}: not valid YAML ({type(exc).__name__})") from exc
    return extension_mapping({} if data is None else data, label)


def extension_mapping(data: object, label: str) -> dict:
    """The same keys from an already-parsed policy (APM's ``effective_policy``)."""
    if not isinstance(data, dict):
        raise PolicyError(f"{label}: a policy must be a YAML mapping")
    result: dict[str, object] = {}
    enforcement = data.get("enforcement")
    if enforcement is not None:
        if enforcement not in ENFORCEMENTS:
            raise PolicyError(f"{label}: enforcement must be one of off, warn, block")
        result["enforcement"] = enforcement
    extends = data.get("extends")
    if extends is not None:
        if not isinstance(extends, str) or not extends.strip():
            raise PolicyError(f"{label}: extends must be a string")
        result["extends"] = extends.strip()
    dependencies = data.get("dependencies") or {}
    if not isinstance(dependencies, dict):
        raise PolicyError(f"{label}: dependencies must be a mapping")
    deny = dependencies.get("deny")
    if deny is not None:
        result["deny_dependencies"] = _strings(deny, f"{label}: dependencies.deny")
    security = data.get("security") or {}
    integrity = security.get("integrity") if isinstance(security, dict) else None
    if security and not isinstance(security, dict):
        raise PolicyError(f"{label}: security must be a mapping")
    if integrity is not None and not isinstance(integrity, dict):
        raise PolicyError(f"{label}: security.integrity must be a mapping")
    hashes = (integrity or {}).get("deny_hashes")
    if hashes is not None:
        values = _strings(hashes, f"{label}: security.integrity.deny_hashes")
        digests = []
        for value in values:
            match = _DIGEST.fullmatch(value)
            if match is None:
                raise PolicyError(
                    f"{label}: security.integrity.deny_hashes entry {value!r} is not "
                    "sha256:<64 lowercase hex>"
                )
            digests.append(match.group(1))
        result["deny_hashes"] = tuple(digests)
    factories = data.get("factories")
    if factories is not None:
        result.update(_factories(factories, label))
    return result


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PolicyError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _factories(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise PolicyError(f"{label}: factories must be a mapping")
    unknown = sorted(set(map(str, value)) - _FACTORY_KEYS)
    if unknown:
        raise PolicyError(
            f"{label}: unknown key factories.{unknown[0]} (expected allow, require_signed, signers)"
        )
    result: dict[str, object] = {}
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
        signers = []
        if not isinstance(value["signers"], list):
            raise PolicyError(f"{label}: factories.signers must be a list")
        for index, item in enumerate(value["signers"]):
            where = f"{label}: factories.signers[{index}]"
            if not isinstance(item, dict) or set(map(str, item)) != _SIGNER_KEYS:
                raise PolicyError(f"{where} must be a mapping with only workflow")
            match = _WORKFLOW.fullmatch(str(item["workflow"]))
            if not isinstance(item["workflow"], str) or match is None:
                raise PolicyError(
                    f"{where}.workflow must be owner/repo/.github/workflows/<file>.yml[@<ref>]"
                )
            signers.append(Signer(match.group("workflow"), match.group("ref")))
        result["signers"] = tuple(signers)
    return result


def merge(documents: Iterable[dict], *, source: str, enforcement: str | None) -> FactoryPolicy:
    """Tighten-only and order-independent, like APM's ``extends`` merge.

    Deny lists are unioned, ``require_signed`` is true if any level sets it,
    and an allowlist or signer list set at several levels keeps only entries
    every level allows.
    """
    allow: dict[str, str | None] | None = None
    unpinned: list[str] = []
    signers: set[Signer] | None = None
    require_signed = False
    deny_hashes: list[str] = []
    deny_dependencies: list[str] = []
    levels: list[str] = []
    for document in documents:
        if "enforcement" in document:
            levels.append(str(document["enforcement"]))
        if "allow" in document:
            entries = {digest: name for name, digest in document["allow"]}
            allow = (
                entries
                if allow is None
                else {
                    digest: allow[digest] or name
                    for digest, name in entries.items()
                    if digest in allow
                }
            )
            unpinned += [p for p in document.get("unpinned", ()) if p not in unpinned]
        if "signers" in document:
            listed = set(document["signers"])
            signers = listed if signers is None else signers & listed
        require_signed = require_signed or bool(document.get("require_signed"))
        deny_hashes += [d for d in document.get("deny_hashes", ()) if d not in deny_hashes]
        deny_dependencies += [
            d for d in document.get("deny_dependencies", ()) if d not in deny_dependencies
        ]
    if enforcement not in ENFORCEMENTS:
        enforcement = max(levels, key=ENFORCEMENTS.index) if levels else "warn"
    return FactoryPolicy(
        source=source,
        enforcement=enforcement,
        allow=None if allow is None else tuple((name, d) for d, name in sorted(allow.items())),
        unpinned=tuple(unpinned),
        require_signed=require_signed,
        signers=None if signers is None else tuple(sorted(signers, key=lambda s: s.workflow)),
        deny_hashes=tuple(deny_hashes),
        deny_dependencies=tuple(deny_dependencies),
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
    """Ask the bundled APM to discover, load and resolve the policy chain."""
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
    """Fetch the bytes of one policy APM resolved (the adapter's only I/O)."""
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


class ExtensionReader:
    """The temporary seam: apmx keys from the exact policies APM resolved.

    ``status`` asks the bundled APM; ``read`` fetches one resolved policy's
    bytes. Both are injectable so every default and fail-closed case is tested
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
    reader: ExtensionReader | None = None,
) -> Resolution:
    """Discover the policy for this audit. Never raises for policy problems."""
    reader = reader or ExtensionReader()
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
        source, kind, label = (
            str((root / POLICY_FILE).resolve()),
            "file",
            f"{POLICY_FILE} (repository)",
        )
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


def _resolve(
    reader: ExtensionReader,
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
            document = extension_keys(reader.read(source, root), label)
        except (PolicyError, OSError, UnicodeDecodeError) as exc:
            return _unloadable(label, exc)
        policy = merge([document], source=label, enforcement=None)
        resolution = Resolution(
            "found", f"{label}   enforcement {policy.enforcement}", policy=policy
        )
        if "extends" in document:
            resolution.status = "partial"
            resolution.notes.append(
                f"extends {document['extends']} not resolved (offline); its rules were not applied"
            )
        resolution.facts["policy"] = {"source": label, "enforcement": policy.enforcement}
        return resolution
    try:
        report = reader.status(root, source)
    except PolicyError as exc:
        return _unloadable(label, exc)
    outcome = str(report.get("outcome"))
    # APM 0.33.0 calls a policy with only apmx's (to it unknown) sections "empty". Until
    # APM returns effective_policy, the adapter decides; otherwise rules would be dropped.
    interim_empty = outcome == "empty" and "effective_policy" not in report
    if interim_empty:
        outcome = "found"
    if outcome in _ABSENT and (kind == "org" or outcome in ("empty", "disabled")):
        where = "APM found no org policy" if kind == "org" else f"{label} is {outcome}"
        return Resolution(
            "none",
            "none found",
            notes=[f"no {POLICY_FILE} rules apply: {where}"],
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
    apm_source = source if kind == "file" else (source or "org")
    if "effective_policy" in report:
        return _effective(report, label, apm_source)
    documents: list[dict] = []
    chain: list[str] = []
    ref: str | None = source if kind == "file" else resolved or source
    try:
        while ref is not None:
            if len(chain) >= _MAX_CHAIN or ref in chain:
                raise PolicyError("extends chain is cyclic or deeper than 5 policies")
            chain.append(ref)
            document = extension_keys(reader.read(ref, root), _label(ref, root))
            documents.append(document)
            ref = document.get("extends")
            if ref == "org":
                parent = reader.status(root, None)
                if str(parent.get("outcome")) not in _LOADED or not parent.get("source"):
                    raise PolicyError("extends org: APM could not resolve the org policy")
                ref = str(parent["source"])
    except (PolicyError, OSError, UnicodeDecodeError) as exc:
        return _unloadable(label, exc)
    policy = merge(documents, source=label, enforcement=report.get("enforcement"))
    if interim_empty and not policy.rules:
        return Resolution(
            "none",
            "none found",
            notes=[f"no {POLICY_FILE} rules apply: {label} is empty"],
            facts={"policy": None},
        )
    resolution = Resolution(
        "found",
        f"{label}   enforcement {policy.enforcement}",
        policy=policy,
        apm_source=apm_source,
        facts={
            "policy": {
                "source": label,
                "enforcement": policy.enforcement,
                "chain": [_label(ref, root) for ref in chain],
            }
        },
    )
    if len(chain) > 1:
        resolution.notes.append("extends " + " <- ".join(_label(r, root) for r in chain))
    return resolution


def _effective(report: dict, label: str, apm_source: str | None) -> Resolution:
    """APM >= microsoft/apm#3232 returns the merged policy itself: no adapter I/O at all."""
    effective = report["effective_policy"]
    if effective is None:
        return Resolution(
            "none", "none found", notes=["APM resolved no effective policy"], facts={"policy": None}
        )
    try:
        document = extension_mapping(effective, label)
    except PolicyError as exc:
        return _unloadable(label, exc)
    policy = merge([document], source=label, enforcement=report.get("enforcement"))
    return Resolution(
        "found",
        f"{label}   enforcement {policy.enforcement}",
        policy=policy,
        apm_source=apm_source,
        facts={"policy": {"source": label, "enforcement": policy.enforcement, "via": "apm"}},
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
        return Verdict("pass", f"{base}; policy has no factories.allow")
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


def _matches(name: str, pattern: str) -> bool:
    """APM's dependency glob: ``*`` within one segment, ``**`` across segments."""
    regex = "".join(
        ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
        for part in re.split(r"(\*\*|\*)", pattern.lower())
    )
    return re.fullmatch(regex, name.lower()) is not None


def _identities(dependency: dict) -> set[str]:
    names = set()
    for value in (dependency.get("repository"), dependency.get("name")):
        if isinstance(value, str) and value:
            names.add(value.removeprefix("github.com/"))
            if dependency.get("virtualPath"):
                names.add(f"{value.removeprefix('github.com/')}/{dependency['virtualPath']}")
    return names


def _hashes(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    text = value.lower().removeprefix("sha256:")
    return {text} if re.fullmatch(r"[0-9a-f]{64}", text) else set()


def evaluate_revocation(
    policy: FactoryPolicy,
    bindings: list[dict],
    dependencies: list[dict],
    bom: dict,
    *,
    packages: bool = True,
    names: bool = True,
) -> Verdict:
    """No capability may match ``deny_hashes``; packages too when APM did not check them.

    Capability bodies and resources (``purl`` + ``bodySha256``) live only in the
    receipt, so apmx always checks them. Package content hashes and
    ``dependencies.deny`` are APM's own checks inside the Ingredients row; apmx
    applies them here only when APM did not: ``packages`` for package hashes
    (offline, or an APM that predates ``deny_hashes``) and ``names`` for
    ``dependencies.deny`` (APM did not audit with this policy), so a finding is
    never reported twice.
    """
    denied = set(policy.deny_hashes)
    for binding in bindings:
        found = (
            _hashes(binding.get("bodySha256"))
            | {h for r in binding.get("resources") or [] for h in _hashes(r.get("sha256"))}
        ) & denied
        if packages:
            found |= (
                _hashes(binding.get("packageHash")) | _hashes(binding.get("registryHash"))
            ) & denied
        if found:
            return _violation(
                policy,
                f"capability {binding.get('capability')} is revoked "
                f"(sha256:{min(found)[:12]}.. in security.integrity.deny_hashes)",
                (f"purl={binding.get('purl')}  bodySha256={binding.get('bodySha256')}",),
            )
    for dependency in dependencies:
        name = dependency.get("repository") or dependency.get("name")
        found = (
            _hashes(dependency.get("contentHash")) | _hashes(dependency.get("registryHash"))
        ) & denied
        if packages and found:
            return _violation(
                policy,
                f"dependency {name} is revoked "
                f"(sha256:{min(found)[:12]}.. in security.integrity.deny_hashes)",
            )
        for pattern in policy.deny_dependencies if names else ():
            if any(_matches(identity, pattern) for identity in _identities(dependency)):
                return _violation(policy, f"dependency {name} matches dependencies.deny {pattern}")
    for component in bom.get("components") or [] if packages else []:
        found = {
            h
            for item in component.get("hashes") or []
            if isinstance(item, dict) and str(item.get("alg", "")).upper() == "SHA-256"
            for h in _hashes(item.get("content"))
        } & denied
        if found:
            return _violation(
                policy,
                f"component {component.get('name')} is revoked "
                f"(sha256:{min(found)[:12]}.. in security.integrity.deny_hashes)",
                (f"purl={component.get('purl')}",),
            )
    scope = "capabilities and dependencies" if packages or names else "capability bodies"
    total = len(bindings) + (len(dependencies) if packages or names else 0)
    rules = len(policy.deny_hashes) + (len(policy.deny_dependencies) if names else 0)
    subject = f"0 of {total} {scope} revoked"
    if not rules:
        return Verdict("pass", f"{subject}; policy denies no hashes or dependencies")
    detail = f"{subject} ({rules} deny rule{'s' if rules != 1 else ''})"
    notes = () if packages and names else ("package rules: apm audit --ci (Ingredients row)",)
    return Verdict("pass", detail, notes)


def evaluate_unsigned(policy: FactoryPolicy) -> Verdict | None:
    """An unsigned receipt against ``factories.require_signed``."""
    if not policy.require_signed:
        return None
    return _violation(policy, "unsigned, but factories.require_signed is set")
