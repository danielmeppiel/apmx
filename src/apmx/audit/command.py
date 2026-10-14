"""``apmx audit``: verify a receipt row by row and say exactly what it proves.

With no arguments it audits the receipt this context implies: the newest
COMPLETE run under ``./.apm/`` locally, or the receipt a GitHub pull request
adds, with that PR's change bound to it. The policy is discovered like
``apm audit`` (see :mod:`apmx.audit.policy`). Every flag is an override.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import click

from . import change as changes
from . import policy as policies
from . import verifier
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
        path = root
        for part in PurePosixPath(relative).parts:
            path = path / part
            if path.is_symlink():
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


def _policy_row(audit: Audit, resolution: policies.Resolution) -> None:
    notes = [_ascii(note) for note in resolution.notes]
    detail = _ascii(resolution.detail)
    if resolution.status == "unloadable":
        audit.incomplete("Policy", detail, notes)
    elif resolution.status == "partial":
        audit.rows["Policy"] = Row("Policy", "warn", detail, notes)
    elif resolution.status == "none":
        audit.rows["Policy"] = Row("Policy", "info", detail, notes)
    elif resolution.policy is not None and resolution.policy.enforcement == "off":
        audit.rows["Policy"] = Row("Policy", "info", detail, [*notes, "rules are not applied"])
    else:
        audit.rows["Policy"] = Row("Policy", "pass", detail, notes)
    audit.facts.update(resolution.facts)


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
) -> Audit:
    root = receipt_path.absolute()
    if not root.is_dir() or not (root / "index.json").is_file():
        raise ReceiptMissing(
            f"No receipt at {_ascii(display or receipt_path)}: expected a receipt directory "
            "containing index.json (for example .apm/chains/<id>/receipt)."
        )
    if outputs is not None and not outputs.is_dir():
        raise ReceiptMissing(f"--outputs {_ascii(outputs)} is not a directory.")
    resolution = policy or policies.Resolution("none", "none found", facts={"policy": None})
    rules = resolution.policy if resolution.applies else None
    audit = Audit(display or str(receipt_path), selection=selection)
    _policy_row(audit, resolution)
    receipt, failure = verifier.inspect(root)
    failed_at = failure.row if failure else None
    for name in verifier.ROWS:
        if failure is not None and name == failed_at:
            audit.fail(name, _ascii(str(failure)))
        elif failed_at is not None and verifier.ROWS.index(name) > verifier.ROWS.index(failed_at):
            audit.rows[name] = Row(name, "skipped", "not evaluated (earlier failure)")
        else:
            audit.rows[name] = Row(name, "pass", _ascii(receipt.details[name]))
    audit.facts.update(receipt.facts)
    structural = failure is None
    if structural and rules is not None:
        base = audit.rows["Factory"].detail
        verdict = policies.evaluate_factory(rules, receipt.facts["definitionSha256"], base)
        audit.verdict("Factory", verdict, [] if rules.allow is None else [base])
        if rules.allow is None:
            audit.rows["Factory"] = Row("Factory", "pass", base)
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
            matched, detail, notes, facts = changes.bind(
                change,
                prefix,
                verifier.delivered_outputs(receipt),
                changes.receipt_inputs(receipt.index),
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
    apm_audit = audit.facts.get("apmAudit")
    apm_checks = apm_audit.get("checkNames", []) if isinstance(apm_audit, dict) else []
    apm_policy = isinstance(apm_audit, dict) and apm_audit.get("policy") != "none (--no-policy)"
    if not structural:
        audit.rows["Revocation"] = Row("Revocation", "skipped", "not evaluated (earlier failure)")
    elif rules is None:
        reason = {
            "unloadable": "not evaluated (policy could not be loaded)",
            "found": "not evaluated (policy enforcement off)",
        }.get(resolution.status, "no policy: nothing revoked to check")
        audit.rows["Revocation"] = Row("Revocation", "info", reason)
    else:
        audit.verdict(
            "Revocation",
            policies.evaluate_revocation(
                rules,
                list(receipt.bindings),
                list(receipt.definition.get("resolvedDependencies", [])),
                receipt.bom,
                packages="content-hash-denylist" not in apm_checks,
                names=not apm_policy,
            ),
        )
    unsigned = policies.evaluate_unsigned(rules) if rules is not None else None
    if unsigned is None:
        audit.rows["Identity"] = Row(
            "Identity", "warn", "unsigned: proves content binding, not who ran it"
        )
    else:
        audit.verdict("Identity", unsigned, ["receipts are signed in CI; see docs/audit.md"])
    return audit


def to_json(audit: Audit) -> str:
    return json.dumps(
        {
            "schema": SCHEMA,
            "receipt": audit.receipt,
            "selection": audit.selection,
            "result": audit.result,
            "exitCode": audit.exit_code,
            "reason": audit.reason,
            "assurance": ASSURANCE,
            "signed": False,
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
        "physicalLocation": {
            "artifactLocation": {"uri": f"{PurePosixPath(audit.receipt).as_posix()}/index.json"}
        }
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
    lines = [header + "   unsigned", ""]
    for name in DISPLAY:
        row = audit.rows[name]
        lines.append(f"{mark(row.status)} {name:<13} {row.detail}")
        lines.extend(f"{'':<18}{note}" for note in row.notes)
    lines.append("")
    if audit.result == "valid":
        lines.append(f"{mark('pass')} VALID   ({ASSURANCE})")
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
    "Verify a receipt: integrity (SHA-256 index), standards (in-toto Statement v1, "
    "SLSA Provenance v1, CycloneDX 1.5), factory definition, checks, ingredients, "
    "revocation, outputs and identity.\n\n"
    "With no arguments it picks the receipt and policy for you. Locally: the newest "
    "COMPLETE run under ./.apm/. In a GitHub pull request: the receipt the PR adds, "
    "with the PR's change (merge-base..head) bound to its outputs. The policy is "
    "discovered like `apm audit`: --policy, else the repository's apm-policy.yml "
    "(the PR base copy in a PR), else the org policy from the git remote. Its "
    "factories and security.integrity.deny_hashes sections add the Factory, "
    "Revocation and Identity rules. No policy is reported, never assumed.\n\n"
    "Ingredients are audited by the bundled pinned APM: the receipt's apm.yml and "
    "apm.lock.yaml are reinstalled with `apm install --frozen` in a temporary project "
    "and checked with `apm audit --ci`.\n\n"
    "Exit codes: 0 valid; 1 invalid or policy failure; 2 usage error, missing or "
    "ambiguous receipt, unloadable policy, or ingredients that could not be audited "
    "(network/auth); use --offline to skip them."
)


def _select(
    receipt: Path | None, cwd: Path, pull: changes.PullRequest | None
) -> tuple[Path, str, str | None, changes.Change | None, str]:
    """Return (receipt, display, selection note, PR change, outputs prefix)."""
    change = None
    root = policies.repository_root(cwd)
    if pull is not None:
        try:
            change = changes.Change.load(root, pull)
        except changes.ChangeUnavailable as exc:
            raise ReceiptMissing(f"{pull.label} change unavailable: {_ascii(exc)}") from exc
    if receipt is not None:
        prefix = changes.workspace_prefix(receipt, root) if change else None
        return receipt, str(receipt), None, change, prefix or ""
    if change is not None:
        found = change.receipts()
        if not found:
            raise ReceiptMissing(
                f"{pull.label} adds no receipt (.apm/<runs|chains|controllers>/<id>/receipt). "
                "Commit the factory's receipt with its outputs, or pass RECEIPT_DIR."
            )
        if len(found) > 1:
            raise ReceiptMissing(
                f"{pull.label} adds {len(found)} receipts ({', '.join(found[:3])}); "
                "pass the one to audit as RECEIPT_DIR."
            )
        path = root / found[0]
        prefix = changes.workspace_prefix(path, root) or ""
        return path, changes.relative_display(path, cwd), f"added by {pull.label}", change, prefix
    latest = changes.latest_local(cwd)
    if latest is None:
        raise ReceiptMissing(
            "No receipt under ./.apm/: run a factory first (every COMPLETE run delivers one), "
            "or pass RECEIPT_DIR."
        )
    path, count = latest
    note = "newest COMPLETE run" if count == 1 else f"newest of {count} COMPLETE runs"
    return path, changes.relative_display(path, cwd), note, None, ""


@click.command(
    name="audit",
    help=AUDIT_HELP,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("receipt", required=False, type=click.Path(path_type=Path), metavar="[RECEIPT_DIR]")
@click.option(
    "--outputs",
    type=click.Path(path_type=Path),
    metavar="DIR",
    help="Override: re-hash delivered files in DIR instead of binding the PR change.",
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
        "Do not launch APM or use the network: ingredients and org policy are reported "
        "as not checked (a warning). Only a local policy file is read."
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
@click.pass_context
def audit_command(
    ctx: click.Context,
    receipt: Path | None,
    outputs: Path | None,
    policy: str | None,
    offline: bool,
    output_format: str,
) -> None:
    cwd = Path.cwd()
    if offline and policy:
        candidate = Path(policy).expanduser()
        if not (candidate if candidate.is_absolute() else cwd / candidate).is_file():
            raise click.UsageError(
                "--offline can only read a local --policy file; org, owner/repo and URLs need "
                "the network."
            )
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
        result = run_audit(
            path,
            outputs=outputs,
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
