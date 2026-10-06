"""Native entrypoint for local factory directories and explicit leaf contracts."""

import os
import sys
from pathlib import Path

import click

from apmx.commands.contracts import invoke_contract
from apmx.contracts.frontend import admit_caller_policy
from apmx.contracts.models import ChainResult, ContractError, ContractLimits, Outcome, RunResult
from apmx.contracts.records import CompletionBoundary, preparation_failure
from apmx.core.contract_logger import ContractLogger
from apmx.core.output_mode import configure_output_mode, detect_output_mode
from apmx.core.tls_trust import configure_process_tls_trust
from apmx.install.contract_source import prepare_contract_source
from apmx.version import get_version


class NativeCommand(click.Command):
    """Keep the frozen MCP helper internal, without adding a public CLI operation."""

    def main(self, *args, **kwargs):
        if getattr(sys, "frozen", False) and os.environ.get("APMX_INTERNAL_ARTIFACT_SERVER"):
            from apmx.runtime.artifact_mcp import run_server

            code = run_server(Path(os.environ["APMX_INTERNAL_ARTIFACT_SERVER"]))
            if kwargs.get("standalone_mode", True):
                raise SystemExit(code)
            return code
        return super().main(*args, **kwargs)


def _finish_result(
    result: RunResult | ChainResult, completion: CompletionBoundary, logger: ContractLogger
) -> int:
    """Delivery failure is separate from the already-finalized execution outcome."""
    from apmx.contracts.evidence import export_completed

    completion.validate(result)
    logger.render_result(result)
    if result.outcome is not Outcome.COMPLETE:
        return int(result.outcome)
    try:
        package = export_completed(result)
    except (ContractError, OSError, KeyboardInterrupt) as exc:
        logger.evidence_delivery_failed(
            str(exc)
            if isinstance(exc, ContractError)
            else "Export interrupted or files could not be written."
        )
        return 23
    logger.evidence_package(package)
    return int(result.outcome)


@click.command(
    cls=NativeCommand,
    name="apmx",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Run a factory directory or one .contract.md file. "
        "A factory's input and output files determine which steps run first.\n\n"
        "Package entries are relative factory directories or .contract.md paths. Inputs and "
        "retained evidence belong to the calling directory, not the package. "
        "A factory directory is its own input, policy and retained-evidence root."
    ),
)
@click.argument("contract", type=str, metavar="FACTORY_OR_CONTRACT")
@click.option(
    "--from",
    "package_ref",
    metavar="PACKAGE_REF",
    help="Select a factory or contract from an APM source package.",
)
@click.option(
    "--on", "harness", required=True, type=str, help="Agent CLI to use (copilot, opencode)."
)
@click.option("--model", metavar="MODEL", help="Model to use through the selected agent CLI.")
@click.option(
    "--plan", "planning", is_flag=True, help="Show steps and checks without running or downloading."
)
@click.option(
    "--allow-unproven-inputs",
    is_flag=True,
    help="For factories, permit fully checked native handoffs; not certification.",
)
@click.option(
    "--allow-host-access",
    "allow_advisory",
    is_flag=True,
    help="Allow the selected agent CLI and checks to use host files, network and available login details.",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Show detailed planning and execution observations."
)
@click.version_option(version=get_version(), prog_name="apmx")
@click.pass_context
def main(
    ctx: click.Context,
    contract: str,
    package_ref: str | None,
    harness: str,
    model: str | None,
    planning: bool,
    allow_advisory: bool,
    verbose: bool,
    allow_unproven_inputs: bool,
) -> None:
    """Dispatch an explicit directory or file through canonical admission."""
    configure_output_mode(detect_output_mode([]))
    configure_process_tls_trust()
    ctx.ensure_object(dict)
    logger = ContractLogger(verbose=verbose)
    caller_root = Path.cwd().resolve()
    limits = ContractLimits()
    result = None
    completion = CompletionBoundary()
    try:
        selected = Path(contract).expanduser().absolute()
        factory_root = selected if package_ref is None and selected.is_dir() else None
        package_factory = package_ref is not None and not contract.endswith(".contract.md")
        if not planning and factory_root is None and not package_factory:
            logger.execution_context()
        if package_ref is None and factory_root is None and not contract.endswith(".contract.md"):
            raise click.UsageError(
                "Select a local factory directory or one explicit .contract.md file."
            )
        if allow_unproven_inputs and factory_root is None and not package_factory:
            raise click.UsageError("--allow-unproven-inputs requires a factory directory.")
        if package_ref is None:
            result = invoke_contract(
                ctx,
                contract,
                harness=harness,
                model=model,
                verbose=verbose,
                planning=planning,
                completion=completion,
                allow_advisory=allow_advisory,
                logger=logger,
                factory_root=factory_root,
                allow_unproven_inputs=allow_unproven_inputs,
            )
            if result is not None:
                ctx.exit(_finish_result(result, completion, logger))
            return
        admit_caller_policy(caller_root, limits=limits)
        if not planning and not allow_advisory:
            raise ContractError(
                "Copilot and checks can read or change files, use the network, and use "
                "available login details. Run only contracts you trust. Add "
                "--allow-host-access to allow this run; policy still applies. "
                "Use --plan to preview without running.",
                code="advisory_consent_required",
                outcome=Outcome.UNPROVEN,
            )
        logger.start_activity("Preparing package", announce=False)
        with prepare_contract_source(
            package_ref,
            contract,
            caller_root=caller_root,
            planning=planning,
            limits=limits,
            factory=package_factory,
            on_preparation=logger.on_preparation,
            verbose=verbose,
        ) as source:
            logger.stop_activity()
            result = invoke_contract(
                ctx,
                contract,
                harness=harness,
                model=model,
                verbose=verbose,
                planning=planning,
                completion=completion,
                allow_advisory=allow_advisory,
                source=source,
                logger=logger,
                factory_root=source.root / contract if package_factory else None,
                allow_unproven_inputs=allow_unproven_inputs,
            )
        if result is not None:
            ctx.exit(_finish_result(result, completion, logger))
    except ContractError as exc:
        error = preparation_failure(result, exc)
        logger.render_error(error)
        ctx.exit(int(error.outcome))
    except OSError:
        logger.render_error(
            preparation_failure(
                result,
                ContractError(
                    "Package preparation failed. Check source permissions and available space.",
                    code="source_filesystem",
                ),
            )
        )
        ctx.exit(int(Outcome.HALTED))
    except KeyboardInterrupt:
        logger.render_error(
            preparation_failure(
                result,
                ContractError(
                    "Package preparation interrupted. Retry after inspecting any retained run record.",
                    code="cancelled",
                ),
            )
        )
        ctx.exit(int(Outcome.HALTED))
    finally:
        logger.close()


if __name__ == "__main__":
    main()
