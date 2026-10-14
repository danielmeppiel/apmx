"""`apmx audit` policy: discovered like `apm audit`, read through an isolated adapter.

Every default and fail-closed case is exercised with an injected APM status
and policy reader, so no test launches APM or touches the network.
"""

import subprocess
from pathlib import Path

import pytest

from apmx.audit import policy as policies

DIGEST = "a" * 64
OTHER = "b" * 64
WORKFLOW = "acme/factories/.github/workflows/run.yml"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Reader(policies.ExtensionReader):
    """APM status and policy bytes from dictionaries; records every call."""

    def __init__(self, reports: dict, files: dict | None = None) -> None:
        self.reports, self.files, self.calls = reports, files or {}, []
        super().__init__(self._status, self._read)

    def _status(self, root, source):
        self.calls.append(("status", source))
        return self.reports[source]

    def _read(self, ref, root):
        self.calls.append(("read", ref))
        if ref in self.files:
            return self.files[ref]
        return Path(ref).read_text()


def _found(source: str, enforcement: str = "block") -> dict:
    return {"outcome": "found", "source": source, "enforcement": enforcement}


# ------------------------------------------------------------------ parsing


def test_extension_keys_reads_only_apmx_sections() -> None:
    keys = policies.extension_keys(
        f"""
name: org
enforcement: block
extends: org
dependencies:
  deny: [acme/compromised-skill]
mcp:
  deny: [ignored]
security:
  integrity:
    require_hashes: true
    deny_hashes: ["sha256:{DIGEST}"]
factories:
  allow: ["acme/checkout-factory@sha256:{DIGEST}", "sha256:{OTHER}"]
  require_signed: true
  signers:
    - workflow: {WORKFLOW}
      repo: acme/app
""",
        "p",
    )
    assert keys == {
        "enforcement": "block",
        "extends": "org",
        "deny_dependencies": ("acme/compromised-skill",),
        "deny_hashes": (DIGEST,),
        "allow": (("acme/checkout-factory", DIGEST), (None, OTHER)),
        "require_signed": True,
        "signers": (policies.Signer(WORKFLOW, "acme/app"),),
    }


@pytest.mark.parametrize(
    "text",
    [
        "factories:\n  require_sigend: true\n",
        "factories:\n  allow: [acme/factory]\n",
        f"factories:\n  allow: ['sha256:{DIGEST[:-1]}']\n",
        "factories:\n  require_signed: 'yes'\n",
        "factories:\n  signers: [{workflow: run.yml}]\n",
        f"factories:\n  signers: [{{workflow: {WORKFLOW}, ref: main}}]\n",
        "security:\n  integrity:\n    deny_hashes: [deadbeef]\n",
        "enforcement: strict\n",
        "- not a mapping\n",
        "name: [\n",
    ],
)
def test_ambiguous_policy_is_unloadable_never_narrowed(text: str) -> None:
    with pytest.raises(policies.PolicyError):
        policies.extension_keys(text, "p")


def test_merge_is_tighten_only_and_order_independent() -> None:
    parent = {
        "allow": ((None, DIGEST), (None, OTHER)),
        "signers": (policies.Signer(WORKFLOW),),
        "deny_hashes": (DIGEST,),
        "enforcement": "warn",
    }
    child = {
        "allow": (("acme/f", DIGEST),),
        "require_signed": True,
        "deny_hashes": (OTHER,),
        "deny_dependencies": ("acme/x",),
        "enforcement": "block",
    }
    for order in ([parent, child], [child, parent]):
        merged = policies.merge(order, source="s", enforcement=None)
        assert merged.allow == (("acme/f", DIGEST),)
        assert merged.signers == (policies.Signer(WORKFLOW),)
        assert set(merged.deny_hashes) == {DIGEST, OTHER}
        assert merged.deny_dependencies == ("acme/x",)
        assert merged.require_signed and merged.enforcement == "block"
    assert policies.merge([{}], source="s", enforcement=None).enforcement == "warn"
    assert policies.merge([child], source="s", enforcement="off").enforcement == "off"


# ------------------------------------------------------------------ discovery


def test_no_file_and_no_remote_is_none_found_without_launching_apm(tmp_path) -> None:
    reader = Reader({})
    resolution = policies.resolve(tmp_path, reader=reader)
    assert (resolution.status, resolution.detail) == ("none", "none found")
    assert resolution.policy is None and not resolution.applies and reader.calls == []


def test_repository_file_is_loaded_through_apm(tmp_path) -> None:
    path = tmp_path / "apm-policy.yml"
    path.write_text(f"enforcement: block\nfactories:\n  allow: ['sha256:{DIGEST}']\n")
    source = str(path.resolve())
    reader = Reader({source: _found(f"file:{source}")})
    resolution = policies.resolve(tmp_path, reader=reader)
    assert resolution.status == "found" and resolution.applies
    assert resolution.detail == "apm-policy.yml (repository)   enforcement block"
    assert resolution.policy.allow == ((None, DIGEST),)
    assert resolution.apm_source == source
    assert reader.calls[0] == ("status", source)


@pytest.mark.parametrize(
    "report",
    [
        {"outcome": "malformed", "error": "Invalid policy file"},
        {"outcome": "incomplete_chain", "error": "Policy chain incomplete"},
        {"outcome": "cache_miss_fetch_fail", "error": "timeout"},
        {"outcome": "hash_mismatch", "error": "pin"},
    ],
)
def test_policy_apm_cannot_load_is_unloadable(tmp_path, report) -> None:
    path = tmp_path / "apm-policy.yml"
    path.write_text("enforcement: block\n")
    reader = Reader({str(path.resolve()): report})
    resolution = policies.resolve(tmp_path, reader=reader)
    assert resolution.status == "unloadable" and not resolution.applies
    assert "could not be loaded" in resolution.detail and report["error"] in resolution.detail


def test_apmx_keys_apm_accepts_but_apmx_cannot_parse_are_unloadable(tmp_path) -> None:
    path = tmp_path / "apm-policy.yml"
    path.write_text("factories:\n  require_sigend: true\n")
    reader = Reader({str(path.resolve()): _found("file:x")})
    resolution = policies.resolve(tmp_path, reader=reader)
    assert resolution.status == "unloadable"
    assert "factories.require_sigend" in resolution.detail


def test_missing_bundled_apm_is_unloadable_not_none(tmp_path) -> None:
    (tmp_path / "apm-policy.yml").write_text("enforcement: block\n")

    def broken(root, source):
        raise policies.PolicyError("bundled APM unavailable: missing")

    resolution = policies.resolve(tmp_path, reader=policies.ExtensionReader(status=broken))
    assert resolution.status == "unloadable" and "bundled APM unavailable" in resolution.detail


def test_explicit_policy_that_is_absent_is_unloadable(tmp_path) -> None:
    reader = Reader({"acme/policy": {"outcome": "absent", "source": "org:acme/policy"}})
    resolution = policies.resolve(tmp_path, override="acme/policy", reader=reader)
    assert resolution.status == "unloadable"
    assert "acme/policy (--policy)" in resolution.detail and "not found" in resolution.detail


def test_org_policy_is_discovered_from_the_remote(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/acme/app.git")
    absent = Reader({None: {"outcome": "absent"}})
    resolution = policies.resolve(tmp_path, reader=absent)
    assert resolution.status == "none" and "no org policy" in resolution.notes[0]
    found = Reader(
        {None: _found("org:acme/.github", "warn")},
        {"org:acme/.github": "factories:\n  require_signed: true\n"},
    )
    resolution = policies.resolve(tmp_path, reader=found)
    assert resolution.status == "found"
    assert resolution.detail == "org:acme/.github (org)   enforcement warn"
    assert resolution.policy.require_signed and resolution.apm_source == "org"
    assert resolution.origin == "https://github.com/acme/app.git"


def test_repository_policy_extends_org_merges_tighten_only(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/acme/app.git")
    path = tmp_path / "apm-policy.yml"
    path.write_text(f"extends: org\nfactories:\n  allow: ['sha256:{DIGEST}', 'sha256:{OTHER}']\n")
    source = str(path.resolve())
    reader = Reader(
        {source: _found(f"file:{source}"), None: _found("org:acme/.github")},
        {"org:acme/.github": f"factories:\n  allow: ['sha256:{DIGEST}']\n  require_signed: true\n"},
    )
    resolution = policies.resolve(tmp_path, reader=reader)
    assert resolution.status == "found"
    assert resolution.policy.allow == ((None, DIGEST),) and resolution.policy.require_signed
    assert resolution.facts["policy"]["chain"] == ["apm-policy.yml", "org:acme/.github"]


def test_cyclic_extends_is_unloadable(tmp_path) -> None:
    reader = Reader(
        {"acme/a": _found("org:acme/a")},
        {"org:acme/a": "extends: acme/b\n", "acme/b": "extends: org:acme/a\n"},
    )
    resolution = policies.resolve(tmp_path, override="acme/a", reader=reader)
    assert resolution.status == "unloadable" and "cyclic" in resolution.detail


def test_pull_request_reads_the_repository_policy_from_the_base(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    path = tmp_path / "apm-policy.yml"
    path.write_text("enforcement: block\nfactories:\n  require_signed: true\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    path.write_text("enforcement: off\n")

    class Status(Reader):
        def _status(self, root, source):
            self.calls.append(("status", source))
            return _found(f"file:{source}")

    reader = Status({})
    resolution = policies.resolve(tmp_path, base=base, reader=reader)
    assert resolution.policy.require_signed and resolution.policy.enforcement == "block"
    assert resolution.detail.startswith(f"apm-policy.yml (repository, PR base {base[:12]})")
    copy = Path(resolution.apm_source)
    assert copy.read_text().startswith("enforcement: block") and copy != path
    resolution.cleanup()
    assert not copy.exists()


def test_offline_reads_a_local_file_without_apm_and_flags_unresolved_extends(tmp_path) -> None:
    path = tmp_path / "apm-policy.yml"
    path.write_text("factories:\n  require_signed: true\n")
    reader = Reader({})
    resolution = policies.resolve(tmp_path, offline=True, reader=reader)
    assert resolution.status == "found" and resolution.policy.enforcement == "warn"
    assert all(call[0] == "read" for call in reader.calls)
    path.write_text("extends: org\nenforcement: block\n")
    resolution = policies.resolve(tmp_path, offline=True, reader=reader)
    assert resolution.status == "partial" and "not resolved (offline)" in resolution.notes[0]


def test_offline_org_policy_is_reported_unchecked(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/acme/app.git")
    resolution = policies.resolve(tmp_path, offline=True, reader=Reader({}))
    assert resolution.status == "partial" and "offline" in resolution.detail


def test_unsupported_remote_policy_hosts_fail_closed(tmp_path) -> None:
    with pytest.raises(policies.PolicyError, match="cannot read factories"):
        policies._read_raw("gitlab.example.com/acme/policy", tmp_path)


# ------------------------------------------------------------------ evaluation


def _policy(**kwargs) -> policies.FactoryPolicy:
    return policies.FactoryPolicy(source="apm-policy.yml", **{"enforcement": "block", **kwargs})


def test_factory_allowlist_follows_enforcement() -> None:
    allowed = _policy(allow=(("acme/f", DIGEST),))
    verdict = policies.evaluate_factory(allowed, DIGEST, "base")
    assert verdict.status == "pass" and "on factories.allow (acme/f)" in verdict.detail
    assert policies.evaluate_factory(allowed, OTHER, "base").status == "fail"
    warn = _policy(allow=(), enforcement="warn")
    assert policies.evaluate_factory(warn, OTHER, "base").status == "warn"
    assert policies.evaluate_factory(_policy(), OTHER, "base").status == "pass"


def test_revocation_matches_content_wherever_it_was_installed_from() -> None:
    binding = {
        "capability": "style",
        "purl": "pkg:generic/style@x",
        "bodySha256": DIGEST,
        "packageHash": f"sha256:{OTHER}",
        "resources": [],
    }
    revoked = _policy(deny_hashes=(DIGEST,))
    verdict = policies.evaluate_revocation(revoked, [binding], [], {})
    assert verdict.status == "fail" and "capability style is revoked" in verdict.detail
    package = _policy(deny_hashes=(OTHER,), enforcement="warn")
    assert policies.evaluate_revocation(package, [binding], [], {}).status == "warn"
    clean = policies.evaluate_revocation(_policy(deny_hashes=("c" * 64,)), [binding], [], {})
    assert clean.status == "pass" and clean.detail.startswith("0 of 1")


def test_revocation_applies_dependencies_deny_with_apm_globs() -> None:
    dependency = {"repository": "github.com/acme/compromised-skill", "name": "compromised-skill"}
    for pattern in ("acme/compromised-skill", "acme/*", "**/compromised-skill"):
        verdict = policies.evaluate_revocation(
            _policy(deny_dependencies=(pattern,)), [], [dependency], {}
        )
        assert verdict.status == "fail" and pattern in verdict.detail
    verdict = policies.evaluate_revocation(
        _policy(deny_dependencies=("other/*",)), [], [dependency], {}
    )
    assert verdict.status == "pass"


def test_revocation_checks_cyclonedx_component_hashes() -> None:
    bom = {"components": [{"name": "c", "hashes": [{"alg": "SHA-256", "content": DIGEST}]}]}
    verdict = policies.evaluate_revocation(_policy(deny_hashes=(DIGEST,)), [], [], bom)
    assert verdict.status == "fail" and "component c" in verdict.detail


def test_unsigned_receipts_against_require_signed() -> None:
    assert policies.evaluate_unsigned(_policy()) is None
    assert policies.evaluate_unsigned(_policy(require_signed=True)).status == "fail"
    warn = _policy(require_signed=True, enforcement="warn")
    assert policies.evaluate_unsigned(warn).status == "warn"
