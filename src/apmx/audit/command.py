"""``apmx audit``: verify a receipt row by row and say exactly what it proves."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import click

from . import verifier
from .ingredients import FAIL, PASS, UNAVAILABLE, WARN, audit_ingredients, resolve_policy

SCHEMA = "apmx-audit/1"
ASSURANCE = "content-bound; not authenticated"
DISPLAY = ("Integrity", "Standards", "Factory", "Checks", "Ingredients", "Outputs", "Identity")
SYMBOLS = {"pass": "[+]", "fail": "[x]", "warn": "[!]", "info": "[i]", "skipped": "[i]"}
COLORS = {"pass": "green", "fail": "red", "warn": "yellow", "info": "cyan", "skipped": None}
EXIT_VALID, EXIT_INVALID, EXIT_UNAVAILABLE = 0, 1, 2


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

    def fail(self, name: str, detail: str, notes: list[str] | None = None) -> None:
        self.rows[name] = Row(name, "fail", detail, notes or [])
        if self.result != "invalid":
            self.result, self.reason, self.exit_code = "invalid", f"{name}: {detail}", EXIT_INVALID


class ReceiptMissing(click.ClickException):
    exit_code = EXIT_UNAVAILABLE


def _ascii(text: str) -> str:
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


def run_audit(
    receipt_path: Path,
    *,
    outputs: Path | None = None,
    policy: str | None = None,
    offline: bool = False,
    display: str | None = None,
) -> Audit:
    root = receipt_path.absolute()
    if not root.is_dir() or not (root / "index.json").is_file():
        raise ReceiptMissing(
            f"No receipt at {_ascii(display or receipt_path)}: expected a receipt directory "
            "containing index.json (for example .apm/chains/<id>/receipt)."
        )
    if outputs is not None and not outputs.is_dir():
        raise ReceiptMissing(f"--outputs {_ascii(outputs)} is not a directory.")
    audit = Audit(display or str(receipt_path))
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
    if failure is None and outputs is not None:
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
    if audit.result != "valid":
        audit.rows["Ingredients"] = Row(
            "Ingredients", "skipped", "not evaluated (receipt invalid; APM not launched)"
        )
    else:
        result = audit_ingredients(receipt, policy=policy, offline=offline)
        audit.facts.update(result.facts)
        status = {PASS: "pass", WARN: "warn", FAIL: "fail", UNAVAILABLE: "warn"}[result.status]
        audit.rows["Ingredients"] = Row(
            "Ingredients", status, _ascii(result.detail), [_ascii(n) for n in result.notes]
        )
        if result.status == FAIL:
            audit.result, audit.exit_code = "invalid", EXIT_INVALID
            audit.reason = f"Ingredients: {_ascii(result.detail)}"
        elif result.status == UNAVAILABLE:
            audit.result, audit.exit_code = "incomplete", EXIT_UNAVAILABLE
            audit.reason = f"Ingredients: {_ascii(result.detail)}"
    audit.rows["Identity"] = Row(
        "Identity", "warn", "unsigned: proves content binding, not who ran it"
    )
    return audit


def to_json(audit: Audit) -> str:
    return json.dumps(
        {
            "schema": SCHEMA,
            "receipt": audit.receipt,
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
    "SLSA Provenance v1, CycloneDX 1.5), factory definition, checks, outputs and "
    "ingredients. Ingredients are audited by the bundled pinned APM: the receipt's "
    "apm.yml and apm.lock.yaml are reinstalled with `apm install --frozen` in a "
    "temporary project and checked with `apm audit --ci` (with --no-policy unless "
    "--policy is given).\n\n"
    "Receipts are unsigned: VALID means the files are bound by content to this factory, "
    "its ingredients and its checks, not who ran it.\n\n"
    "Exit codes: 0 valid; 1 invalid or policy failure; 2 usage error, missing receipt, "
    "or ingredients that could not be audited (network/auth); use --offline to skip them."
)


@click.command(
    name="audit",
    help=AUDIT_HELP,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.argument("receipt", type=click.Path(path_type=Path), metavar="RECEIPT_DIR")
@click.option(
    "--outputs",
    type=click.Path(path_type=Path),
    metavar="DIR",
    help="Re-hash delivered files in DIR (for example a PR checkout) against receipt subjects.",
)
@click.option(
    "--policy",
    metavar="SOURCE",
    help="Passed to `apm audit --policy`: org, owner/repo, an https:// URL or a file.",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Do not launch APM; ingredients are reported as not audited (a warning, not a failure).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format; json is for CI.",
)
@click.pass_context
def audit_command(
    ctx: click.Context,
    receipt: Path,
    outputs: Path | None,
    policy: str | None,
    offline: bool,
    output_format: str,
) -> None:
    if offline and policy:
        raise click.UsageError("--policy needs APM; it cannot be combined with --offline.")
    try:
        result = run_audit(
            receipt,
            outputs=outputs,
            policy=resolve_policy(policy, Path.cwd()),
            offline=offline,
            display=str(receipt),
        )
    except ReceiptMissing as exc:
        if output_format == "json":
            click.echo(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "receipt": _ascii(receipt),
                        "result": "error",
                        "exitCode": EXIT_UNAVAILABLE,
                        "reason": exc.message,
                    },
                    indent=2,
                )
            )
        raise
    if output_format == "json":
        click.echo(to_json(result))
    else:
        color = _color_enabled()
        click.echo(to_text(result, color=color), color=color)
    ctx.exit(result.exit_code)
