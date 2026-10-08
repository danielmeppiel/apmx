"""Native entrypoint for factories, source packages and explicit leaf contracts."""

import os
import sys
from collections.abc import Callable
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
from apmx.install.contract_source_validation import validate_reference
from apmx.models.dependency.reference import DependencyReference
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

    if logger.presentation is not None:
        logger.presentation.phase("Finalizing")
        logger.presentation.result(result)
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


def _select_entry(
    contract: str | None, package_ref: str | None
) -> tuple[str, str | None, Path | None]:
    """Expand package shorthand without changing existing local workspace roots."""
    if package_ref is not None:
        return "." if contract is None else contract, package_ref, None
    if contract is None:
        raise click.UsageError(
            "No factory selected. Pass a local directory, .contract.md file, "
            "Git package reference, or --from PACKAGE_REF."
        )
    selected = Path(contract).expanduser().absolute()
    if selected.is_dir():
        return contract, None, selected
    if contract.endswith(".contract.md"):
        return contract, None, None
    if selected.exists() or selected.is_symlink():
        raise click.UsageError(
            "The selected local entry is not a factory directory or .contract.md file. "
            "Choose a factory directory or an explicit .contract.md file."
        )
    try:
        dependency = DependencyReference.parse(contract)
    except ValueError as exc:
        raise click.UsageError(
            "Selection is not a local factory directory, .contract.md file, or Git package "
            "reference. Use ./PATH for a local factory or --from PACKAGE_REF for a package."
        ) from exc
    if dependency.is_local:
        raise click.UsageError(
            "Local factory directory was not found. Check the path, or use "
            "--from PACKAGE_REF to run a package against the current project."
        )
    validate_reference(dependency)
    return ".", contract, None


@click.command(
    cls=NativeCommand,
    name="apmx",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Run a Git package, local factory directory or one .contract.md file. "
        "A factory's input and output files determine which steps run first.\n\n"
        "Packages default to the whole factory at their root. With --from, an optional "
        "entry selects a package-relative directory or .contract.md file. Package inputs "
        "and evidence belong to the calling directory.\n\n"
        "An existing positional directory is its own input, policy and evidence root. "
        "Use ./PATH to require a local directory; use --from to select a source package "
        "even when a local directory has the same name."
    ),
)
@click.argument("contract", required=False, type=str, metavar="[FACTORY_OR_CONTRACT_OR_PACKAGE]")
@click.option(
    "--from",
    "package_ref",
    metavar="PACKAGE_REF",
    help="Run an APM source package against the current project; default entry: package root.",
)
@click.option(
    "--on",
    "harness",
    default="copilot",
    show_default=True,
    type=str,
    help="Agent CLI to use (copilot, opencode).",
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
    "--verbose",
    "-v",
    is_flag=True,
    help="Stream native debug logs and show source/process details.",
)
@click.option(
    "--tui",
    "tui",
    is_flag=True,
    help=(
        "Show the factory graph in the Textual UI: --tui --plan previews it "
        "without running anything; --tui alone runs the factory live through "
        "it, with the same default-No consent prompt as a normal run (see "
        "docs/textual-design.md). Needs a factory directory and an interactive "
        "terminal on both ends; redirected/CI runs and single contract files "
        "stay on the plain output."
    ),
)
@click.option(
    "--no-tui",
    "no_tui",
    is_flag=True,
    help="Reserved escape hatch: the TUI is never auto-selected yet, so this is currently a no-op.",
)
@click.version_option(version=get_version(), prog_name="apmx")
@click.pass_context
def main(
    ctx: click.Context,
    contract: str | None,
    package_ref: str | None,
    harness: str,
    model: str | None,
    planning: bool,
    allow_advisory: bool,
    verbose: bool,
    allow_unproven_inputs: bool,
    tui: bool,
    no_tui: bool,
) -> None:
    """Dispatch the selected factory or file through canonical admission."""
    if tui and no_tui:
        raise click.UsageError("Pass either --tui or --no-tui, not both.")
    configure_output_mode(detect_output_mode([]))
    configure_process_tls_trust()
    ctx.ensure_object(dict)
    logger = ContractLogger(verbose=verbose)
    if tui:
        from apmx.tui.entry import tui_eligible

        try:
            entry, source_ref, factory = _select_entry(contract, package_ref)
            if factory is None and (source_ref is None or entry.endswith(".contract.md")):
                raise ContractError(
                    "The TUI needs a factory directory, not a single contract file.",
                    code="tui_requires_factory",
                    outcome=Outcome.UNPROVEN,
                )
            if not tui_eligible():
                raise ContractError(
                    "The TUI needs an interactive terminal on both ends; omit --tui.",
                    code="tui_unavailable",
                    outcome=Outcome.UNPROVEN,
                )
            from apmx.commands.tui_live import launch_workspace

            def command(presentation, cancel_requested):
                logger.presentation = presentation
                logger._display.disable()
                with ctx:
                    _invoke(
                        ctx,
                        contract,
                        package_ref,
                        harness,
                        model,
                        planning,
                        allow_advisory,
                        verbose,
                        allow_unproven_inputs,
                        logger,
                        cancel_requested,
                    )

            code = launch_workspace(
                source=source_ref or str(factory),
                entry=entry,
                planning=planning,
                command=command,
            )
        except ContractError as exc:
            logger.render_error(exc)
            code = int(exc.outcome)
        ctx.exit(code)
    _invoke(
        ctx,
        contract,
        package_ref,
        harness,
        model,
        planning,
        allow_advisory,
        verbose,
        allow_unproven_inputs,
        logger,
    )


def _invoke(
    ctx: click.Context,
    contract: str | None,
    package_ref: str | None,
    harness: str,
    model: str | None,
    planning: bool,
    allow_advisory: bool,
    verbose: bool,
    allow_unproven_inputs: bool,
    logger: ContractLogger,
    cancel_requested: Callable[[], bool] | None = None,
) -> None:
    """Canonical command, including acquisition teardown and evidence delivery."""
    caller_root = Path.cwd().resolve()
    limits = ContractLimits()
    result = None
    completion = CompletionBoundary()
    try:
        contract, package_ref, factory_root = _select_entry(contract, package_ref)
        package_factory = package_ref is not None and not contract.endswith(".contract.md")
        if not planning and factory_root is None and not package_factory:
            logger.execution_context()
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
                cancel_requested=cancel_requested,
            )
            if result is not None:
                ctx.exit(_finish_result(result, completion, logger))
            return
        admit_caller_policy(caller_root, limits=limits)
        if (
            not planning
            and not allow_advisory
            and not (
                package_factory
                and not allow_unproven_inputs
                and logger.can_confirm_factory()
                and logger.confirm_package_preparation(package_ref)
            )
        ):
            raise ContractError(
                f"{ContractLogger._harness_label(harness)} and checks can read or change files, "
                "use the network, and use "
                "available login details. Run only contracts you trust. Add "
                "--allow-host-access to allow this run; policy still applies. "
                "Use --plan to preview without running.",
                code="advisory_consent_required",
                outcome=Outcome.UNPROVEN,
            )
        logger.start_activity("Loading factory and dependencies", announce=False)
        with prepare_contract_source(
            package_ref,
            contract,
            caller_root=caller_root,
            planning=planning,
            limits=limits,
            factory=package_factory,
            on_preparation=logger.on_preparation,
            verbose=verbose,
            **({"cancel_requested": cancel_requested} if cancel_requested else {}),
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
                cancel_requested=cancel_requested,
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
