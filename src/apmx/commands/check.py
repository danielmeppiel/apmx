"""``apmx check``: re-check another run's outputs here, where no agent ran.

The CI producer boundary. An agent runs as the runner user, so it could
rewrite its own job's check scripts or results. ``apmx check`` therefore takes
only the **output bytes** from the producer's receipt (untrusted data, keyed by
contract digest) and runs the factory from *this* checkout: each contract's
outputs are replayed into a fresh workspace, the factory's own checks run on
them, and a new receipt of record is written. The producer's checks, record
and claims are never reused.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import click

from apmx.audit import verifier
from apmx.runtime.replay import ENV_BUNDLE

CHECK_HELP = (
    "Re-check a producer's outputs on this machine and write the receipt of record.\n\n"
    "PRODUCER_RECEIPT is the receipt of a run made elsewhere, for example by an agent job in "
    "CI. Only its output bytes are used, matched to contracts by SHA-256; its checks, records "
    "and claims are ignored. FACTORY (default: the current directory) is run from this "
    "checkout with the replay producer: no agent runs, the factory's own checks run on the "
    "replayed outputs, and a new receipt is written under FACTORY/.apm/. The new receipt "
    "must have the same factory definition digest as the producer's.\n\n"
    "With --from PACKAGE_REF, the factory is that APM package (FACTORY then selects an "
    "optional package-relative entry) run against the current directory, as in "
    "`apmx --from PACKAGE_REF`; the receipt is written under ./.apm/. The definition digest "
    "covers the package's resolved commit and content, so a different package, ref or "
    "commit withdraws the receipt.\n\n"
    "Checks run with host access, as in `apmx FACTORY --allow-host-access`. "
    "Exit codes are those of a factory run; 1 also means the producer receipt is invalid "
    "or its definition differs."
)


class CheckError(click.ClickException):
    exit_code = 1


def extract_outputs(receipt: Path, destination: Path) -> tuple[str, int]:
    """Write each selected attempt's artifacts to ``destination/<contract sha256>/``."""
    parsed, failure = verifier.inspect(receipt)
    if failure is not None:
        raise CheckError(f"The producer receipt is invalid ({failure.row}: {failure}).")
    count = 0
    seen: set[str] = set()
    for attempt in parsed.index["attempts"]:
        if not attempt["selected"]:
            continue
        run_id = attempt["runId"]
        statement = parsed.statements[f"provenance/{run_id}.intoto.json"]
        digest = statement["predicate"]["buildDefinition"]["externalParameters"]["contractSha256"]
        if digest in seen:
            raise CheckError(
                f"The producer receipt has two runs of contract sha256:{digest[:12]}.."
            )
        seen.add(digest)
        prefix = f"attempts/{run_id}/artifacts/"
        for name in parsed.index["files"]:
            if name.startswith(prefix):
                target = destination / digest / name[len(prefix) :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(verifier.read(parsed.root, name))
                count += 1
    return parsed.index["definition"]["digest"]["sha256"], count


def _newest_receipt(root: Path, before: set[Path]) -> Path | None:
    found = {
        path.parent
        for kind in ("runs", "chains", "controllers")
        for path in (root / ".apm" / kind).glob("*/receipt/index.json")
    }
    created = sorted(found - before, key=lambda path: path.parent.name)
    return created[-1] if created else None


@click.command(
    name="check", help=CHECK_HELP, context_settings={"help_option_names": ["-h", "--help"]}
)
@click.argument("producer", type=click.Path(path_type=Path), metavar="PRODUCER_RECEIPT")
@click.argument("factory", required=False, default=".", metavar="[FACTORY]")
@click.option(
    "--from",
    "package_ref",
    metavar="PACKAGE_REF",
    help="Check an APM source package's outputs against the current directory.",
)
@click.option("--verbose", "-v", is_flag=True, help="Stream native debug logs.")
def check_command(producer: Path, factory: str, package_ref: str | None, verbose: bool) -> None:
    from apmx.cli import main as run

    if not (producer / "index.json").is_file():
        raise click.UsageError(f"No receipt at {producer}: expected a directory with index.json.")
    if package_ref is not None:
        root = Path.cwd().absolute()
        selection = ([] if factory == "." else [factory]) + ["--from", package_ref]
    else:
        root = Path(factory).expanduser().absolute()
        if not root.is_dir():
            raise click.UsageError(
                "FACTORY must be a local factory directory; use --from PACKAGE_REF for a package."
            )
        selection = [factory if factory != "." else "./"]
    before = {
        path.parent
        for kind in ("runs", "chains", "controllers")
        for path in (root / ".apm" / kind).glob("*/receipt/index.json")
    }
    with tempfile.TemporaryDirectory(prefix="apmx-check-") as temporary:
        bundle = Path(temporary) / "outputs"
        bundle.mkdir()
        definition, count = extract_outputs(producer, bundle)
        click.echo(
            f"[i] Replaying {count} recorded output file{'s' if count != 1 else ''} from "
            f"{producer} (definition sha256:{definition[:12]}..); no agent runs here."
        )
        previous = os.environ.get(ENV_BUNDLE)
        os.environ[ENV_BUNDLE] = str(bundle)
        try:
            arguments = [*selection, "--on", "replay"]
            # Handoffs between contracts are checked by this run before they are used.
            arguments += ["--allow-host-access", "--allow-unproven-inputs"]
            arguments += ["--verbose"] if verbose else []
            code = run.main(arguments, prog_name="apmx", standalone_mode=False)
        finally:
            if previous is None:
                os.environ.pop(ENV_BUNDLE, None)
            else:
                os.environ[ENV_BUNDLE] = previous
    code = int(code or 0)
    if code != 0:
        raise SystemExit(code)
    receipt = _newest_receipt(root, before)
    if receipt is None:
        raise CheckError("The check run completed but delivered no receipt.")
    index = json.loads((receipt / "index.json").read_bytes())
    actual = index["definition"]["digest"]["sha256"]
    if actual != definition:
        shutil.rmtree(receipt)
        raise CheckError(
            f"This checkout's factory definition sha256:{actual[:12]}.. differs from the "
            f"producer's sha256:{definition[:12]}..; the receipt was withdrawn. Check out the "
            "commit the producer ran"
            + (", with the same package ref and commit." if package_ref else ".")
        )
    click.echo(f"[+] Checked here: receipt of record {receipt}")
