"""``apmx audit``: verify a factory delivery row by row and say exactly what it proves.

With no arguments it audits what this context implies: the newest COMPLETE
run's ``receipt.intoto.jsonl`` under ``./.apm/`` locally, or, in a GitHub pull
request (which carries code only), the signed attestations its changed files
are subjects of, looked up by digest. The policy is discovered like
``apm audit`` (see :mod:`apmx.audit.policy`). Every flag is an override.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import click

from . import change as changes
from . import explain as explanation
from . import identity as identities
from . import policy as policies
from . import stages, verifier
from .ingredients import FAIL, PASS, UNAVAILABLE, WARN, audit_ingredients

SCHEMA = "apmx-audit/1"
ASSURANCE = "content-bound; not authenticated"
DISPLAY = (
    "Policy",
    "Integrity",
    "Standards",
    "Factory",
    "Checks",
    "Ingredients",
    "Revocation",
    "Outputs",
    "Identity",
)
SYMBOLS = {"pass": "[+]", "fail": "[x]", "warn": "[!]", "info": "[i]", "skipped": "[i]"}
COLORS = {"pass": "green", "fail": "red", "warn": "yellow", "info": "cyan", "skipped": None}
EXIT_VALID, EXIT_INVALID, EXIT_UNAVAILABLE = 0, 1, 2
SARIF_LEVELS = {"fail": "error", "warn": "warning"}
NOT_FACTORY = (
    "Not a factory delivery: no changed file carries an attestation from factories.signers. "
    "This check only passes factory deliveries; merge other PRs through your normal review "
    "process (e.g. a ruleset bypass for maintainers)."
)
MAX_LOOKUPS = 1000


@dataclass
class Row:
    name: str
    status: str
    detail: str
    notes: list[str] = field(default_factory=list)


@dataclass
class Audit:
    receipt: str
    rows: dict[str, Row] = field(default_factory=dict)
    facts: dict[str, object] = field(default_factory=dict)
    result: str = "valid"
    reason: str | None = None
    exit_code: int = EXIT_VALID
    selection: str | None = None
    signer: str | None = None

    @property
    def assurance(self) -> str:
        return f"authenticated: {self.signer}" if self.signer else ASSURANCE

    def fail(self, name: str, detail: str, notes: list[str] | None = None) -> None:
        self.rows[name] = Row(name, "fail", detail, notes or [])
        if self.result != "invalid":
            self.result, self.reason, self.exit_code = "invalid", f"{name}: {detail}", EXIT_INVALID

    def incomplete(self, name: str, detail: str, notes: list[str] | None = None) -> None:
        self.rows[name] = Row(name, "warn", detail, notes or [])
        if self.result == "valid":
            self.result, self.reason = "incomplete", f"{name}: {detail}"
            self.exit_code = EXIT_UNAVAILABLE

    def verdict(self, name: str, verdict: policies.Verdict, notes: list[str] | None = None) -> None:
        extra = [*map(_ascii, verdict.notes), *(notes or [])]
        if verdict.status == "fail":
            self.fail(name, _ascii(verdict.detail), extra)
        else:
            self.rows[name] = Row(name, verdict.status, _ascii(verdict.detail), extra)


class ReceiptMissing(click.ClickException):
    exit_code = EXIT_UNAVAILABLE


def _ascii(text: object) -> str:
    return "".join(char if " " <= char <= "~" else "?" for char in str(text))


def _rehash_outputs(receipt: verifier.Receipt, directory: Path) -> tuple[bool, str]:
    """Compare explicitly delivered files with the receipt's bound subjects."""
    expected = verifier.delivered_outputs(receipt)
    root = directory.absolute()
    for relative, digest in sorted(expected.items()):
        path = _local(root, relative)
        if path is None:
            return False, f"{relative} is a symlink in --outputs; refused"
        try:
            info = path.stat()
        except FileNotFoundError:
            return False, f"{relative} is missing from --outputs"
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
            return False, f"{relative} in --outputs is not a bounded regular file"
        if verifier.sha256(path.read_bytes()) != digest:
            return False, f"{relative} differs from its receipt subject (SHA-256)"
    count = len(expected)
    return True, f"{count}/{count} delivered files match receipt subjects (re-hashed)"


def _local(root: Path, relative: str) -> Path | None:
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            return None
    return path


def _policy_row(audit: Audit, resolution: policies.Resolution) -> None:
    notes = [_ascii(note) for note in resolution.notes]
    detail = _ascii(resolution.detail)
    if resolution.status == "unloadable":
        audit.incomplete("Policy", detail, notes)
    elif resolution.status == "partial":
        audit.rows["Policy"] = Row("Policy", "warn", detail, notes)
    elif resolution.status == "none" or not resolution.applies:
        audit.rows["Policy"] = Row("Policy", "info", detail, notes)
    else:
        audit.rows["Policy"] = Row("Policy", "pass", detail, notes)
    audit.facts.update(resolution.facts)


def _structural(audit: Audit, receipt: verifier.Receipt, failure: verifier.Failure | None) -> None:
    failed_at = failure.row if failure else None
    for name in verifier.ROWS:
        if failure is not None and name == failed_at:
            audit.fail(name, _ascii(str(failure)))
        elif failed_at is not None and verifier.ROWS.index(name) > verifier.ROWS.index(failed_at):
            audit.rows[name] = Row(name, "skipped", "not evaluated (earlier failure)")
        else:
            audit.rows[name] = Row(name, "pass", _ascii(receipt.details[name]))
    audit.facts.update(receipt.facts)
    if failure is None:
        audit.facts["stages"] = stages.factory_stages(receipt)


def _policy_rows(
    audit: Audit,
    receipt: verifier.Receipt,
    resolution: policies.Resolution,
    *,
    structural: bool,
    offline: bool,
) -> None:
    """Factory allow-list, Ingredients and Revocation: the same in every route."""
    rules = resolution.policy if resolution.applies else None
    if structural and rules is not None:
        base = audit.rows["Factory"].detail
        verdict = policies.evaluate_factory(rules, receipt.definition_sha256, base)
        audit.verdict("Factory", verdict, [] if rules.allow is None else [base])
        if rules.allow is None:
            audit.rows["Factory"] = Row("Factory", "pass", base)
    if audit.result == "invalid":
        audit.rows["Ingredients"] = Row(
            "Ingredients", "skipped", "not evaluated (receipt invalid; APM not launched)"
        )
    elif resolution.status == "unloadable":
        audit.rows["Ingredients"] = Row(
            "Ingredients", "skipped", "not evaluated (policy could not be loaded)"
        )
    else:
        result = audit_ingredients(
            receipt,
            policy=resolution.apm_source,
            offline=offline,
            origin=resolution.origin,
        )
        audit.facts.update(result.facts)
        status = {PASS: "pass", WARN: "warn", FAIL: "fail", UNAVAILABLE: "warn"}[result.status]
        audit.rows["Ingredients"] = Row(
            "Ingredients", status, _ascii(result.detail), [_ascii(n) for n in result.notes]
        )
        if result.status == FAIL:
            audit.fail("Ingredients", _ascii(result.detail), audit.rows["Ingredients"].notes)
        elif result.status == UNAVAILABLE:
            audit.incomplete("Ingredients", _ascii(result.detail), audit.rows["Ingredients"].notes)
    if not structural:
        audit.rows["Revocation"] = Row("Revocation", "skipped", "not evaluated (earlier failure)")
    elif rules is None:
        reason = {
            "unloadable": "not evaluated (policy could not be loaded)",
            "found": "not evaluated (top-level enforcement: off)",
        }.get(resolution.status, "no policy: nothing revoked to check")
        audit.rows["Revocation"] = Row("Revocation", "info", reason)
    elif not rules.present:
        audit.rows["Revocation"] = Row("Revocation", "info", "policy has no factories section")
    else:
        audit.verdict("Revocation", policies.evaluate_revocation(rules, list(receipt.bindings)))


def _bundle_binding(receipt: verifier.Receipt) -> identities.Accept:
    """A summary must bind exactly this bundle's statements, files and definition."""
    lines = sorted(receipt.facts["lineDigests"])

    def accept(summary: dict) -> str | None:
        predicate = summary.get("predicate") or {}
        try:
            named = sorted(
                item["digest"]["sha256"] for item in predicate.get("inputAttestations") or []
            )
            verifier.vsa_prefix(summary, receipt.subjects)
        except (KeyError, TypeError, ValueError):
            return "summary does not name this delivery's files"
        if named != lines:
            return "summary binds different statements than this receipt"
        if ((predicate.get("policy") or {}).get("digest") or {}).get(
            "sha256"
        ) != receipt.definition_sha256:
            return "summary names a different factory definition"
        return None

    return accept


def run_audit(
    receipt_path: Path,
    *,
    outputs: Path | None = None,
    policy: policies.Resolution | None = None,
    offline: bool = False,
    display: str | None = None,
    change: changes.Change | None = None,
    prefix: str = "",
    selection: str | None = None,
    run: identities.Runner | None = None,
) -> Audit:
    """Audit an explicit bundle (the offline route), optionally binding a PR change."""
    path = receipt_path.absolute()
    if path.is_dir():
        path = path / verifier.BUNDLE
    if not path.is_file():
        raise ReceiptMissing(
            f"No receipt at {_ascii(display or receipt_path)}: expected {verifier.BUNDLE} "
            "(for example .apm/chains/<id>/receipt.intoto.jsonl)."
        )
    if outputs is not None and not outputs.is_dir():
        raise ReceiptMissing(f"--outputs {_ascii(outputs)} is not a directory.")
    resolution = policy or policies.Resolution("none", "none found", facts={"policy": None})
    rules = resolution.policy if resolution.applies else None
    audit = Audit(display or str(receipt_path), selection=selection)
    _policy_row(audit, resolution)
    receipt, failure = verifier.inspect(path)
    _structural(audit, receipt, failure)
    structural = failure is None
    if structural and outputs is not None:
        try:
            matched, detail = _rehash_outputs(receipt, outputs)
        except (OSError, ValueError) as exc:
            matched, detail = False, f"--outputs could not be read ({_ascii(exc)})"
        audit.facts["outputsRehashed"] = matched
        if matched:
            audit.rows["Outputs"].notes.append(audit.rows["Outputs"].detail)
            audit.rows["Outputs"].detail = _ascii(detail)
        else:
            audit.fail("Outputs", _ascii(detail))
    elif structural and change is not None:
        try:
            matched, detail, notes, facts = changes.bind(change, prefix, receipt)
        except (changes.ChangeUnavailable, OSError, ValueError) as exc:
            audit.incomplete("Outputs", f"the PR change could not be read ({_ascii(exc)})")
        else:
            audit.facts["change"] = {**facts, "bound": matched}
            if matched:
                audit.rows["Outputs"] = Row(
                    "Outputs", "pass", _ascii(detail), [*notes, audit.rows["Outputs"].detail]
                )
            else:
                audit.fail("Outputs", _ascii(detail), notes)
    _policy_rows(audit, receipt, resolution, structural=structural, offline=offline)
    artifact = None
    if structural:
        for root in (outputs, path.parent / verifier.OUTPUTS):
            if root is None or not root.is_dir():
                continue
            for name, digest in sorted(receipt.subjects.items()):
                candidate = _local(root.absolute(), name)
                if (
                    candidate is not None
                    and candidate.is_file()
                    and verifier.sha256(candidate.read_bytes()) == digest
                ):
                    artifact = candidate
                    break
            if artifact is not None:
                break
    _identity(
        audit,
        rules,
        structural=structural,
        offline=offline,
        artifact=artifact,
        accept=_bundle_binding(receipt) if structural else None,
        run=run,
    )
    return audit


def _identity(
    audit: Audit,
    rules: policies.FactoryPolicy | None,
    *,
    structural: bool,
    offline: bool,
    artifact: Path | None,
    accept: identities.Accept | None,
    run: identities.Runner | None = None,
) -> identities.Identity | None:
    """Verify a signed summary only against the workflows the policy trusts."""
    if rules is None:
        audit.rows["Identity"] = Row(
            "Identity", "warn", "unsigned: proves content binding, not who ran it"
        )
        return None
    if not structural:
        audit.rows["Identity"] = Row("Identity", "skipped", "not evaluated (earlier failure)")
        return None
    if not rules.signers:
        detail = "not authenticated: policy names no factories.signers"
        verdict = policies.evaluate_unsigned(
            rules, "factories.require_signed is set but factories.signers is empty"
        )
        if verdict is None:
            audit.rows["Identity"] = Row("Identity", "warn", detail)
        else:
            audit.verdict("Identity", verdict)
        return None
    if offline:
        result = identities.Identity("unavailable", "signature not checked (offline)")
    elif artifact is None or accept is None:
        result = identities.Identity(
            "unavailable", "signature not checked: no delivered file to look it up by (--outputs)"
        )
    else:
        result = identities.verify(
            artifact,
            accept,
            rules.signers,
            default_repository=identities.repository(policies.repository_root(Path.cwd())),
            run=run,
        )
    _identity_row(audit, rules, result)
    return result


def _identity_row(audit: Audit, rules: policies.FactoryPolicy, result: identities.Identity) -> None:
    audit.facts["identity"] = {"status": result.status, **result.facts}
    notes = [_ascii(note) for note in result.notes]
    if result.status == "signed":
        audit.signer = result.signer
        audit.rows["Identity"] = Row("Identity", "pass", _ascii(result.detail), notes)
    elif result.status == "unsigned":
        verdict = policies.evaluate_unsigned(rules, result.detail)
        if verdict is None:
            audit.rows["Identity"] = Row("Identity", "warn", _ascii(result.detail), notes)
        else:
            audit.verdict("Identity", verdict, notes)
    elif result.status == "mismatch":
        audit.verdict("Identity", policies.evaluate_signer_sha(rules, result.detail), notes)
    elif result.status == "unresolved" or rules.require_signed:
        audit.incomplete("Identity", _ascii(result.detail), notes)
    else:
        audit.rows["Identity"] = Row("Identity", "warn", _ascii(result.detail), notes)


def _not_evaluated(audit: Audit, reason: str) -> None:
    for name in DISPLAY:
        if name not in audit.rows:
            audit.rows[name] = Row(name, "skipped", reason)


def run_gate(
    change: changes.Change,
    *,
    policy: policies.Resolution,
    offline: bool = False,
    run: identities.Runner | None = None,
) -> Audit:
    """Audit a code-only PR from the attestation store, by its files' digests."""
    audit = Audit(f"attestations for {change.pull.label}", selection="looked up by file digest")
    _policy_row(audit, policy)
    rules = policy.policy if policy.applies else None
    if policy.status == "unloadable":
        _not_evaluated(audit, "not evaluated (policy could not be loaded)")
        return audit
    if rules is None or not rules.signers:
        audit.incomplete(
            "Identity",
            "a PR carries code only: its delivery is looked up from signed attestations, "
            "and the policy names no factories.signers",
        )
        _not_evaluated(audit, "not evaluated (no signer to look attestations up from)")
        return audit
    present = sorted(path for path, status in change.changed.items() if status != "D")
    head = change.files(change.pull.head, present) if present else {}
    wanted = {path: verifier.sha256(raw) for path, (_, raw) in head.items() if raw is not None}
    if offline:
        audit.incomplete("Identity", "signature not checked (offline)")
        _not_evaluated(audit, "not evaluated (no signed delivery covers this PR)")
        return audit
    attested = _factory_file(audit, change, rules, wanted, run)
    if attested is None:
        return audit
    present = [attested, *(path for path in present if path != attested)]
    if set(wanted) != set(present):
        missing = min(set(present) - set(wanted))
        audit.fail("Outputs", f"{change.pull.label} changes {missing} into a non-regular file")
        _not_evaluated(audit, "not evaluated (earlier failure)")
        return audit

    def covers(summary: dict) -> str | None:
        subjects = {
            item.get("name"): (item.get("digest") or {}).get("sha256")
            for item in summary.get("subject") or []
            if isinstance(item, dict)
        }
        missing = [path for path, digest in wanted.items() if subjects.get(path) != digest]
        return f"summary does not cover {missing[0]}" if missing else None

    with tempfile.TemporaryDirectory(prefix="apmx-gate-") as temporary:
        artifact = Path(temporary) / "artifact"
        result = identities.Identity("unavailable", "signature not checked")
        # A summary is looked up by a changed file's digest, starting with the file that
        # carries a factory attestation; files the delivery does not name have none.
        for first in present[:10]:
            artifact.write_bytes(head[first][1])
            result = identities.verify(
                artifact,
                covers,
                rules.signers,
                default_repository=identities.repository(change.root),
                run=run,
            )
            if result.status != "unsigned" or result.notes:
                break
        if result.status != "signed":
            if result.status == "unsigned":
                uncovered = [note for note in result.notes if "does not cover" in note]
                result.detail = (
                    f"no PASSED verification summary from factories.signers covers every "
                    f"changed file ({uncovered[0].rsplit('does not cover ', 1)[1].rstrip(')')} "
                    f"is not a delivered file)"
                    if uncovered
                    else "no PASSED verification summary from factories.signers covers the "
                    "changed files"
                )
                audit.facts["identity"] = {"status": result.status, **result.facts}
                audit.fail("Identity", _ascii(result.detail), [_ascii(n) for n in result.notes])
            else:
                _identity_row(audit, rules, result)
                if audit.result == "valid":
                    # Nothing in a code-only PR is bound until a signed summary is found.
                    audit.incomplete(
                        "Identity", _ascii(result.detail), audit.rows["Identity"].notes
                    )
            _not_evaluated(audit, "not evaluated (no signed delivery covers this PR)")
            return audit
        try:
            carriers = identities.statements(artifact, result, (verifier.BUNDLE_TYPE,), run=run)
        except (OSError, ValueError) as exc:
            _identity_row(audit, rules, result)
            audit.incomplete("Integrity", f"statements could not be fetched ({_ascii(exc)})")
            _not_evaluated(audit, "not evaluated (statements unavailable)")
            return audit
    _identity_row(audit, rules, result)
    by_digest = {}
    for carrier in carriers:
        lines = (carrier.get("predicate") or {}).get("lines")
        for item in lines if isinstance(lines, list) else []:
            try:
                normalized = verifier.normalized(item)
            except (KeyError, TypeError, ValueError):
                continue
            by_digest[verifier.line_digest(normalized)] = normalized
    summary = verifier.normalized(result.summary)
    statements = []
    for entry in summary["predicate"].get("inputAttestations") or []:
        digest = (entry.get("digest") or {}).get("sha256")
        if digest not in by_digest:
            audit.fail(
                "Integrity",
                f"input attestation {_ascii(entry.get('uri') or digest)} is not in the "
                "signer's attestation store",
            )
            _not_evaluated(audit, "not evaluated (earlier failure)")
            return audit
        statements.append(by_digest[digest])
    receipt, failure = verifier.inspect_statements([*statements, summary])
    _structural(audit, receipt, failure)
    structural = failure is None
    if structural:
        try:
            matched, detail, notes, facts = changes.bind(
                change, str(receipt.facts["subjectPrefix"]), receipt
            )
        except (changes.ChangeUnavailable, OSError, ValueError) as exc:
            audit.incomplete("Outputs", f"the PR change could not be read ({_ascii(exc)})")
        else:
            audit.facts["change"] = {**facts, "bound": matched}
            if matched:
                audit.rows["Outputs"] = Row(
                    "Outputs", "pass", _ascii(detail), [*notes, audit.rows["Outputs"].detail]
                )
            else:
                audit.fail("Outputs", _ascii(detail), notes)
    _policy_rows(audit, receipt, policy, structural=structural, offline=offline)
    return audit


def _factory_file(
    audit: Audit,
    change: changes.Change,
    rules: policies.FactoryPolicy,
    wanted: dict[str, str],
    run: identities.Runner | None,
) -> str | None:
    """The first changed file that carries a factory attestation; else settle the audit.

    A PR none of whose files carries one is not a factory delivery: it fails (exit 1)
    with a message that says how such PRs are merged. A lookup that fails is
    INCOMPLETE (exit 2). Either way the result is never a pass.
    """
    default = identities.repository(change.root)
    repositories = list(dict.fromkeys(signer.repo or default for signer in rules.signers))
    digests = list(dict.fromkeys(wanted.values()))
    facts: dict[str, object] = {
        "factory": False,
        "lookedUp": len(digests),
        "repositories": [repo for repo in repositories if repo],
    }
    audit.facts["delivery"] = facts
    unavailable: list[str] = []
    if None in repositories:
        unavailable.append("a signer has no repository to look up attestations in")
        repositories = [repo for repo in repositories if repo]
    lookups = 0
    seen: set[str] = set()
    for path, digest in sorted(wanted.items()):
        if digest in seen:
            continue
        seen.add(digest)
        for repo in repositories:
            lookups += 1
            if lookups > MAX_LOOKUPS:
                audit.incomplete(
                    "Identity",
                    f"{len(digests)} changed files are too many to look up (at most "
                    f"{MAX_LOOKUPS} attestation lookups)",
                )
                _not_evaluated(audit, "not evaluated (attestations not looked up)")
                return None
            status, reason = identities.carries(digest, repo, run)
            if status == "factory":
                facts.update(factory=True, attested=path, repository=repo)
                return path
            if status == "unavailable":
                unavailable.append(f"{path} in {repo}: {reason}")
    if unavailable:
        audit.incomplete(
            "Identity",
            "attestations could not be looked up",
            [_ascii(note) for note in unavailable[:5]],
        )
        _not_evaluated(audit, "not evaluated (attestations not looked up)")
        return None
    where = ", ".join(facts["repositories"]) or "no repository"
    audit.fail(
        "Identity",
        f"not a factory delivery: none of {len(digests)} changed file digest(s) carries a "
        f"factory attestation in {where}",
    )
    audit.reason = NOT_FACTORY
    _not_evaluated(audit, "not evaluated (not a factory delivery)")
    return None


def to_json(audit: Audit) -> str:
    return json.dumps(
        {
            "schema": SCHEMA,
            "receipt": audit.receipt,
            "selection": audit.selection,
            "result": audit.result,
            "exitCode": audit.exit_code,
            "reason": audit.reason,
            "assurance": audit.assurance,
            "signed": audit.signer is not None,
            "rows": [
                {"name": row.name, "status": row.status, "detail": row.detail, "notes": row.notes}
                for row in (audit.rows[name] for name in DISPLAY)
            ],
            "facts": audit.facts,
        },
        indent=2,
        sort_keys=False,
        ensure_ascii=True,
    )


def to_sarif(audit: Audit) -> str:
    """SARIF 2.1.0 for GitHub code scanning: one result per failing or warning row."""
    from apmx.version import get_version

    location = {
        "physicalLocation": {"artifactLocation": {"uri": PurePosixPath(audit.receipt).as_posix()}}
    }
    results = [
        {
            "ruleId": f"apmx-audit/{row.name.lower()}",
            "level": SARIF_LEVELS[row.status],
            "message": {"text": " ".join([row.detail, *row.notes])},
            "locations": [location],
        }
        for row in (audit.rows[name] for name in DISPLAY if name in audit.rows)
        if row.status in SARIF_LEVELS
    ]
    if audit.result == "error":
        results.append(
            {
                "ruleId": "apmx-audit/receipt",
                "level": "error",
                "message": {"text": audit.reason or "no receipt"},
                "locations": [location],
            }
        )
    rules = [
        {
            "id": f"apmx-audit/{name.lower()}",
            "name": name,
            "shortDescription": {"text": f"apmx audit {name} row"},
            "helpUri": "https://github.com/danielmeppiel/apmx/blob/main/docs/audit.md#rows",
        }
        for name in (*DISPLAY, "Receipt")
    ]
    return json.dumps(
        {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "apmx audit",
                            "version": get_version(),
                            "informationUri": "https://github.com/danielmeppiel/apmx",
                            "rules": rules,
                        }
                    },
                    "results": results,
                    "properties": {"result": audit.result, "exitCode": audit.exit_code},
                }
            ],
        },
        indent=2,
        ensure_ascii=True,
    )


def _color_enabled() -> bool:
    return "NO_COLOR" not in os.environ and sys.stdout.isatty()


def to_text(audit: Audit, *, color: bool = False) -> str:
    def mark(status: str) -> str:
        symbol = SYMBOLS[status]
        if color and COLORS[status]:
            return click.style(symbol, fg=COLORS[status], bold=status == "fail")
        return symbol

    contracts = audit.facts.get("contracts")
    header = f"Receipt   {_ascii(audit.receipt)}"
    if audit.selection:
        header += f"   ({_ascii(audit.selection)})"
    if isinstance(contracts, int):
        header += f"   {contracts} contract{'s' if contracts != 1 else ''}"
    lines = [header + ("   signed" if audit.signer else "   unsigned"), ""]
    for name in DISPLAY:
        row = audit.rows[name]
        lines.append(f"{mark(row.status)} {name:<13} {row.detail}")
        lines.extend(f"{'':<18}{note}" for note in row.notes)
    lines.append("")
    if audit.result == "valid":
        lines.append(f"{mark('pass')} VALID   ({audit.assurance})")
    elif audit.result == "invalid" and audit.reason == NOT_FACTORY:
        lines.append(f"{mark('fail')} {NOT_FACTORY}")
    elif audit.result == "invalid":
        lines.append(f"{mark('fail')} INVALID   {audit.reason}")
    else:
        lines.append(f"{mark('warn')} INCOMPLETE   {audit.reason}")
        lines.append(
            "             Not a pass: the receipt could not be fully audited (exit 2). "
            "--offline skips ingredients explicitly."
        )
    return "\n".join(lines)


AUDIT_HELP = (
    "Verify a factory delivery: integrity, standards (in-toto Statement v1, SLSA "
    "Provenance v1, in-toto Test Result, CycloneDX 1.5, SLSA VSA v1), factory "
    "definition, checks, ingredients, revocation, outputs and identity.\n\n"
    "With no arguments it picks what to audit for you. Locally: the newest COMPLETE "
    "run's receipt.intoto.jsonl under ./.apm/, with its outputs/ re-hashed. In a GitHub "
    "pull request, which carries code only: the PR's changed files (merge-base..head) are "
    "looked up by SHA-256 in the attestation store of the policy's factories.signers; "
    "every changed file must be a subject of one PASSED verification summary from an "
    "allowed signer, which binds the receipt statements by digest; files the PR touches "
    "must match the recorded before-images at the PR base. A PR none of whose files "
    "carries a factory attestation fails as not a factory delivery: the check only passes "
    "factory deliveries. RECEIPT is the offline route: "
    "a receipt.intoto.jsonl (or its run directory), with --outputs DIR.\n\n"
    "The policy is discovered like `apm audit`: --policy, else the repository's "
    "apm-policy.yml (the PR base copy in a PR), else the org policy from the git remote. "
    "Its `factories` section (apmx's own) adds the Factory, Revocation and Identity "
    "rules; APM's keys are enforced by APM. No policy is reported, never assumed.\n\n"
    "Ingredients are audited by the bundled pinned APM: the receipt's inline apm.yml and "
    "apm.lock.yaml are reinstalled with `apm install --frozen` in a temporary project "
    "and checked with `apm audit --ci`.\n\n"
    "--explain [factory|stages|checks|ingredients|outputs] prints what a receipt records; "
    "--export abom writes its CycloneDX 1.5 inventory as a standalone file.\n\n"
    "Exit codes: 0 valid; 1 invalid or policy failure; 2 usage error, missing or "
    "ambiguous receipt, unloadable policy, or ingredients or signatures that could not be "
    "checked (network/auth); use --offline to skip them."
)


def _select(
    receipt: Path | None, cwd: Path, pull: changes.PullRequest | None
) -> tuple[Path | None, str, str | None, changes.Change | None, str]:
    """Return (bundle or None for the PR gate, display, selection note, PR change, prefix)."""
    change = None
    root = policies.repository_root(cwd)
    if pull is not None:
        try:
            change = changes.Change.load(root, pull)
        except changes.ChangeUnavailable as exc:
            raise ReceiptMissing(f"{pull.label} change unavailable: {_ascii(exc)}") from exc
    if receipt is not None:
        bundle = receipt / verifier.BUNDLE if receipt.is_dir() else receipt
        prefix = changes.workspace_prefix(bundle, root) if change else None
        return bundle, str(receipt), None, change, prefix or ""
    if change is not None:
        return None, f"attestations for {pull.label}", None, change, ""
    latest = changes.latest_local(cwd)
    if latest is None:
        raise ReceiptMissing(
            f"No receipt under ./.apm/: run a factory first (every COMPLETE run writes "
            f"{verifier.BUNDLE}), or pass RECEIPT."
        )
    path, count = latest
    note = "newest COMPLETE run" if count == 1 else f"newest of {count} COMPLETE runs"
    return path, changes.relative_display(path, cwd), note, None, ""


def _read_only(bundle: Path, explain: str | None, export: str | None, output: Path | None) -> int:
    """--explain / --export: only for a bundle that verifies structurally."""
    receipt, failure = verifier.inspect(bundle)
    if failure is not None:
        click.echo(f"[x] {failure.row}: {_ascii(failure)}", err=True)
        return EXIT_INVALID
    if export is not None:
        raw = explanation.export_abom(receipt)
        if output is None:
            click.echo(raw.decode("ascii"), nl=False)
        else:
            if output.exists() or output.is_symlink():
                raise click.UsageError(f"{output} exists; refusing to overwrite it.")
            output.write_bytes(raw)
            click.echo(f"[+] CycloneDX 1.5 inventory written to {_ascii(output)}")
        return EXIT_VALID
    click.echo(explanation.explain(receipt, explain or "all"))
    return EXIT_VALID


@click.command(
    name="audit",
    help=AUDIT_HELP,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("receipt", required=False, type=click.Path(path_type=Path), metavar="[RECEIPT]")
@click.option(
    "--outputs",
    type=click.Path(path_type=Path),
    metavar="DIR",
    help="Re-hash the delivered files in DIR against the receipt (offline route).",
)
@click.option(
    "--policy",
    metavar="SOURCE",
    help="Override discovery: org, owner/repo, an https:// URL or an apm-policy.yml file.",
)
@click.option(
    "--offline",
    is_flag=True,
    help=(
        "Do not launch APM or use the network: ingredients, signatures and org policy are "
        "reported as not checked. Only a local policy file is read."
    ),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "sarif"]),
    default="text",
    show_default=True,
    help="Output format; json and sarif are for CI.",
)
@click.option(
    "--explain",
    type=click.Choice([*explanation.TOPICS, "all"]),
    is_flag=False,
    flag_value="all",
    default=None,
    help="Print what the receipt records (default: all topics) instead of auditing it.",
)
@click.option(
    "--export",
    type=click.Choice(["abom"]),
    default=None,
    help="Write the receipt's CycloneDX 1.5 inventory (stdout, or -o FILE).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    metavar="FILE",
    help="With --export: the file to write.",
)
@click.pass_context
def audit_command(
    ctx: click.Context,
    receipt: Path | None,
    outputs: Path | None,
    policy: str | None,
    offline: bool,
    output_format: str,
    explain: str | None,
    export: str | None,
    output: Path | None,
) -> None:
    cwd = Path.cwd()
    if offline and policy:
        candidate = Path(policy).expanduser()
        if not (candidate if candidate.is_absolute() else cwd / candidate).is_file():
            raise click.UsageError(
                "--offline can only read a local --policy file; org, owner/repo and URLs need "
                "the network."
            )
    if output is not None and export is None:
        raise click.UsageError("-o/--output is only used with --export.")
    if explain is not None or export is not None:
        if receipt is None:
            latest = changes.latest_local(cwd)
            if latest is None:
                raise ReceiptMissing("No receipt under ./.apm/: pass RECEIPT.")
            bundle = latest[0]
        else:
            bundle = receipt / verifier.BUNDLE if receipt.is_dir() else receipt
        if not bundle.is_file():
            raise ReceiptMissing(f"No receipt at {_ascii(bundle)}.")
        ctx.exit(_read_only(bundle, explain, export, output))
    pull = changes.pull_request()
    resolution = None
    try:
        path, display, selection, change, prefix = _select(receipt, cwd, pull)
        resolution = policies.resolve(
            cwd,
            override=policy,
            offline=offline,
            base=change.pull.base if change is not None else None,
        )
        if path is None:
            result = run_gate(change, policy=resolution, offline=offline)
        else:
            local = selection is not None and outputs is None
            sibling = path.parent / verifier.OUTPUTS
            result = run_audit(
                path,
                outputs=sibling if local and sibling.is_dir() else outputs,
                policy=resolution,
                offline=offline,
                display=display,
                change=None if outputs is not None else change,
                prefix=prefix,
                selection=selection,
            )
    except ReceiptMissing as exc:
        if output_format == "json":
            click.echo(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "receipt": _ascii(receipt) if receipt is not None else None,
                        "result": "error",
                        "exitCode": EXIT_UNAVAILABLE,
                        "reason": exc.message,
                    },
                    indent=2,
                )
            )
        elif output_format == "sarif":
            error = Audit(_ascii(receipt) if receipt is not None else ".apm", result="error")
            error.exit_code, error.reason = EXIT_UNAVAILABLE, exc.message
            click.echo(to_sarif(error))
        raise
    finally:
        if resolution is not None:
            resolution.cleanup()
    if output_format == "json":
        click.echo(to_json(result))
    elif output_format == "sarif":
        click.echo(to_sarif(result))
    else:
        color = _color_enabled()
        click.echo(to_text(result, color=color), color=color)
    ctx.exit(result.exit_code)
