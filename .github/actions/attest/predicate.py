"""Audit a receipt structurally, then write the apmx receipt predicate to sign.

Runs ``apmx audit --offline`` from an empty directory with the pull-request
context removed, so neither a repository policy nor a PR binding can change
what is being signed: the signature says "this receipt was structurally
VALID when this workflow signed it", nothing more. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PREDICATE_TYPE = "https://github.com/danielmeppiel/apmx/attestation/receipt/v1"
CHECKS = ("same-job", "separate-job")


def build(receipt: Path, checks: str, apmx: str) -> dict:
    if checks not in CHECKS:
        raise ValueError(f"checks must be one of {', '.join(CHECKS)}")
    index = receipt / "index.json"
    if not index.is_file():
        raise ValueError(f"{receipt} is not a receipt directory (no index.json)")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH")
    }
    env["NO_COLOR"] = "1"
    with tempfile.TemporaryDirectory(prefix="apmx-attest-") as neutral:
        completed = subprocess.run(
            [apmx, "audit", str(receipt.resolve()), "--offline", "--format", "json"],
            cwd=neutral,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    try:
        report = json.loads(completed.stdout)
    except ValueError as exc:
        raise ValueError(f"apmx audit produced no report: {completed.stderr.strip()}") from exc
    if completed.returncode != 0 or report.get("result") != "valid":
        raise ValueError(f"refusing to sign: apmx audit says {report.get('reason') or report}")
    version = subprocess.run(
        [apmx, "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "receipt": {
            "name": "index.json",
            "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        },
        "definitionSha256": report["facts"]["definitionSha256"],
        "outputs": report["facts"].get("outputs", []),
        "checks": checks,
        "audit": {"result": "valid", "mode": "apmx audit --offline", "apmx": version},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--checks", default="same-job")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    apmx = shutil.which("apmx")
    if apmx is None:
        print(
            "::error title=apmx attest::apmx is not on PATH; run the setup action first",
            file=sys.stderr,
        )
        return 1
    try:
        predicate = build(args.receipt, args.checks, apmx)
    except (ValueError, KeyError, OSError) as exc:
        print(f"::error title=apmx attest::{exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(predicate, indent=2, sort_keys=True) + "\n")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"predicate={args.output}\n")
        stream.write(f"subject={(args.receipt / 'index.json').resolve()}\n")
        stream.write(f"sha256={predicate['receipt']['sha256']}\n")
        stream.write(f"definition={predicate['definitionSha256']}\n")
    print(
        f"[+] receipt {predicate['receipt']['sha256'][:12]}.. is VALID; "
        f"signing definition {predicate['definitionSha256'][:12]}.. (checks: {args.checks})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
