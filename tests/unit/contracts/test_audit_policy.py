"""`apmx audit` policy: the same apm-policy.yml, with one apmx-owned `factories` section.

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
SHA = "c0ffee" + "0" * 34


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Reader(policies.PolicyReader):
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


def _found(source: str, outcome: str = "found") -> dict:
    return {"outcome": outcome, "source": source, "enforcement": "warn"}


# ------------------------------------------------------------------ parsing

PURL = "pkg:github/acme/style@" + "c" * 40


def test_only_the_factories_section_is_read() -> None:
    keys = policies.document_keys(
        f"""
name: org
enforcement: warn
extends: org
dependencies:
  deny: [acme/compromised-skill]
security:
  integrity:
    deny_hashes: ["sha256:{DIGEST}"]
factories:
  enforcement: warn
  allow: ["acme/checkout-factory@sha256:{DIGEST}", "sha256:{OTHER}", "acme/*"]
  require_signed: true
  signers:
    - workflow: {WORKFLOW}@refs/tags/v1
    - workflow: {WORKFLOW}
      repo: acme/app
  revoked:
    - purl: {PURL}
      bodySha256: {DIGEST}
""",
        "p",
    )
    assert keys == {
        "top_enforcement": "warn",
        "extends": "org",
        "present": True,
        "enforcement": "warn",
        "allow": (("acme/checkout-factory", DIGEST), (None, OTHER)),
        "unpinned": ("acme/*",),
        "require_signed": True,
        "signers": (
            policies.Signer(WORKFLOW, "refs/tags/v1"),
            policies.Signer(WORKFLOW, None, "acme/app"),
        ),
        "revoked": (policies.Revoked(PURL, DIGEST),),
    }


def test_workflow_sha_pins_a_signer_by_field_or_by_sha_ref() -> None:
    keys = policies.document_keys(
        f"""
factories:
  signers:
    - workflow: {WORKFLOW}@refs/tags/v1
      workflow_sha: {SHA.upper()}
    - workflow: {WORKFLOW}@{SHA}
    - workflow: {WORKFLOW}@{SHA}
      workflow_sha: {SHA}
""",
        "p",
    )
    tagged = policies.Signer(WORKFLOW, "refs/tags/v1", None, SHA)
    by_sha = policies.Signer(WORKFLOW, None, None, SHA)
    assert keys["signers"] == (tagged, by_sha, by_sha)
    field = policies.document_keys(
        f"factories:\n  signers: [{{workflow: {WORKFLOW}, workflow_sha: {SHA}}}]\n", "p"
    )
    assert field["signers"] == (by_sha,)


def test_merge_keeps_a_signer_only_if_every_level_allows_it() -> None:
    pinned = policies.Signer(WORKFLOW, workflow_sha=SHA)
    other = policies.Signer(WORKFLOW, workflow_sha="f" * 40)
    unpinned = policies.Signer(WORKFLOW)
    tagged = policies.Signer(WORKFLOW, "refs/tags/v1")

    def merged(*levels):
        results = {
            policies.merge(
                [{"present": True, "signers": level} for level in order], source="s"
            ).signers
            for order in (levels, tuple(reversed(levels)))
        }
        assert len(results) == 1  # order-independent
        return results.pop()

    # A child cannot unpin: the parent's sha survives, the child's unpinned entry does not.
    assert merged((pinned,), (unpinned,)) == (pinned,)
    # A child can pin a signer its parent lists unpinned.
    assert merged((unpinned,), (pinned,)) == (pinned,)
    # Different pins allow nothing; a ref pin does not stand in for a sha pin.
    assert merged((pinned,), (other,)) == ()
    assert merged((pinned,), (tagged,)) == ()
    assert merged((tagged,), (unpinned,)) == (tagged,)
    both = policies.Signer(WORKFLOW, "refs/tags/v1", None, SHA)
    assert merged((tagged,), (pinned,), (both,)) == (both,)
    # A different repository is a different signer.
    assert merged((pinned,), (policies.Signer(WORKFLOW, None, "acme/app"),)) == ()


def test_null_means_no_opinion_and_empty_means_none() -> None:
    keys = policies.document_keys("factories:\n  allow: null\n  signers: null\n", "p")
    assert keys == {"present": True}
    keys = policies.document_keys("factories:\n  allow: []\n  signers: []\n", "p")
    assert keys == {"present": True, "allow": (), "unpinned": (), "signers": ()}
    assert policies.document_keys("name: apm-only\n", "p") == {}


@pytest.mark.parametrize(
    "text",
    [
        "factories:\n  require_sigend: true\n",
        "factories:\n  allow: ['acme/f@sha256:short']\n",
        f"factories:\n  allow: ['sha256:{DIGEST[:-1]}']\n",
        "factories:\n  require_signed: 'yes'\n",
        "factories:\n  enforcement: off\n",
        "factories:\n  signers: [{workflow: run.yml}]\n",
        f"factories:\n  signers: [{{workflow: {WORKFLOW}, digest: abc}}]\n",
        f"factories:\n  signers: [{{workflow: {WORKFLOW}, repo: not-a-repo}}]\n",
        "factories:\n  signers: [{repo: acme/app}]\n",
        f"factories:\n  revoked: [{{purl: {PURL}}}]\n",
        f"factories:\n  revoked: [{{purl: style, bodySha256: {DIGEST}}}]\n",
        f"factories:\n  revoked: [{{purl: {PURL}, bodySha256: '{DIGEST}\\n'}}]\n",
        f"factories:\n  signers: [{{workflow: {WORKFLOW}, workflow_sha: abc123}}]\n",
        f'factories:\n  signers: [{{workflow: {WORKFLOW}, workflow_sha: "{SHA}\\n"}}]\n',
        f"factories:\n  signers: [{{workflow: {WORKFLOW}@{SHA}, workflow_sha: {'f' * 40}}}]\n",
        "factories: [allow]\n",
        "- not a mapping\n",
        "name: [\n",
    ],
)
def test_ambiguous_factories_rules_are_unloadable_never_narrowed(text: str) -> None:
    with pytest.raises(policies.PolicyError):
        policies.document_keys(text, "p")


def test_merge_is_tighten_only_and_order_independent() -> None:
    parent = {
        "present": True,
        "enforcement": "warn",
        "allow": ((None, DIGEST), (None, OTHER)),
        "signers": (policies.Signer(WORKFLOW),),
        "revoked": (policies.Revoked(PURL, DIGEST),),
    }
    child = {
        "present": True,
        "allow": (("acme/f", DIGEST),),
        "require_signed": True,
        "revoked": (policies.Revoked(PURL, OTHER),),
    }
    for order in ([parent, child], [child, parent]):
        merged = policies.merge(order, source="s")
        assert merged.allow == (("acme/f", DIGEST),)
        assert merged.signers == (policies.Signer(WORKFLOW),)
        assert set(merged.revoked) == {
            policies.Revoked(PURL, DIGEST),
            policies.Revoked(PURL, OTHER),
        }
        assert merged.require_signed and merged.enforcement == "block"
    staged = policies.merge([parent, {"present": True, "enforcement": "warn"}], source="s")
    assert staged.enforcement == "warn"
    assert policies.merge([{}], source="s").enforcement == "block"


def test_top_level_enforcement_warn_never_softens_factories_rules() -> None:
    """APM's enforcement: warn is APM's switch; factories rules stay a gate (apm#3195 lesson)."""
    assert policies.merge(
        [{"top_enforcement": "warn", "present": True}], source="s"
    ).enforcement == ("block")
    assert policies.document_keys("enforcement: off\n", "p") == {"top_enforcement": "off"}
    off = policies.merge([{"top_enforcement": "off", "present": True}], source="s")
    assert off.enforcement == "off"
    tighter = [{"top_enforcement": "off", "present": True}, {"top_enforcement": "block"}]
    assert policies.merge(tighter, source="s").enforcement == "block"


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
    assert resolution.detail == "apm-policy.yml (repository)   factories enforcement block"
    assert resolution.policy.allow == ((None, DIGEST),)
    assert resolution.apm_source == source
    assert reader.calls[0] == ("status", source)


def test_apm_0_33_empty_outcome_still_carries_apmx_rules(tmp_path) -> None:
    """APM 0.33.0 reports a factories-only policy as empty; that must not drop the rules."""
    path = tmp_path / "apm-policy.yml"
    path.write_text("factories:\n  require_signed: true\n")
    source = str(path.resolve())
    reader = Reader(
        {source: {"outcome": "empty", "source": f"file:{source}", "enforcement": "warn"}}
    )
    resolution = policies.resolve(tmp_path, reader=reader)
    assert resolution.status == "found" and resolution.policy.require_signed
    assert resolution.apm_source is None  # no APM rules: `apm audit` runs with --no-policy


@pytest.mark.parametrize("outcome", ["empty", "disabled"])
def test_empty_or_disabled_policy_is_none_found(tmp_path, outcome) -> None:
    path = tmp_path / "apm-policy.yml"
    path.write_text("name: empty\n")
    reader = Reader({str(path.resolve()): {"outcome": outcome, "source": f"file:{path}"}})
    resolution = policies.resolve(tmp_path, reader=reader)
    assert (resolution.status, resolution.detail) == ("none", "none found")


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

    resolution = policies.resolve(tmp_path, reader=policies.PolicyReader(status=broken))
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
    assert resolution.status == "none" and "APM reports absent" in resolution.notes[0]
    found = Reader(
        {None: _found("org:acme/.github")},
        {"org:acme/.github": "factories:\n  require_signed: true\n"},
    )
    resolution = policies.resolve(tmp_path, reader=found)
    assert resolution.status == "found"
    assert resolution.detail == "org:acme/.github (org)   factories enforcement block"
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
    assert resolution.status == "found" and resolution.policy.enforcement == "block"
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
    return policies.FactoryPolicy(source="apm-policy.yml", present=True, **kwargs)


def test_factory_allowlist_is_a_gate_unless_staged() -> None:
    allowed = _policy(allow=(("acme/f", DIGEST),))
    verdict = policies.evaluate_factory(allowed, DIGEST, "base")
    assert verdict.status == "pass" and "on factories.allow (acme/f)" in verdict.detail
    assert policies.evaluate_factory(allowed, OTHER, "base").status == "fail"
    staged = _policy(allow=(), enforcement="warn")
    assert policies.evaluate_factory(staged, OTHER, "base").status == "warn"
    assert policies.evaluate_factory(_policy(), OTHER, "base") == policies.Verdict("pass", "base")
    unpinned = policies.evaluate_factory(_policy(allow=(), unpinned=("acme/*",)), DIGEST, "base")
    assert unpinned.status == "fail" and "allow nothing yet" in unpinned.notes[1]


def test_revocation_needs_both_purl_and_body_to_match() -> None:
    binding = {"capability": "style", "purl": PURL, "bodySha256": DIGEST}
    revoked = _policy(revoked=(policies.Revoked(PURL, DIGEST),))
    verdict = policies.evaluate_revocation(revoked, [binding])
    assert verdict.status == "fail" and "capability style is revoked" in verdict.detail
    assert f"bodySha256={DIGEST}" in verdict.notes[0]
    staged = _policy(revoked=(policies.Revoked(PURL, DIGEST),), enforcement="warn")
    assert policies.evaluate_revocation(staged, [binding]).status == "warn"
    for other in (policies.Revoked(PURL, OTHER), policies.Revoked("pkg:github/x/y@1", DIGEST)):
        clean = policies.evaluate_revocation(_policy(revoked=(other,)), [binding])
        assert (
            clean.status == "pass"
            and clean.detail == "0 of 1 capability revoked (1 factories.revoked entry)"
        )
    empty = policies.evaluate_revocation(_policy(), [])
    assert empty.detail == "0 of 0 capabilities revoked; factories.revoked is empty"


def test_unsigned_receipts_against_require_signed() -> None:
    assert policies.evaluate_unsigned(_policy()) is None
    assert policies.evaluate_unsigned(_policy(require_signed=True)).status == "fail"
    staged = _policy(require_signed=True, enforcement="warn")
    assert policies.evaluate_unsigned(staged).status == "warn"
