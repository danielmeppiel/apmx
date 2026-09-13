"""Shared command boundary for explicit contracts, never script fallback."""

from pathlib import Path

import click

from apmx.contracts.events import ImportsSelectedEvent
from apmx.contracts.models import ContractSource
from apmx.core.contract_logger import ContractLogger


def invoke_contract(
    ctx: click.Context,
    contract: str,
    *,
    harness: str,
    model: str | None,
    verbose: bool,
    planning: bool,
    allow_advisory: bool = False,
    source: ContractSource | None = None,
    logger: ContractLogger | None = None,
) -> None:
    """Plan or execute one leaf using the contract-specific error boundary."""
    from ..contracts import frontend, workspace
    from ..contracts.models import ContractError, ContractLimits, Outcome
    from ..install.contract_source import prepare_imports

    if logger is None:
        logger = ContractLogger(verbose=verbose)
    try:
        logger.start_activity("Reading contract", announce=False)
        limits = ContractLimits()
        frontend.admit_caller_policy(Path.cwd(), limits=limits)
        if not planning and not allow_advisory:
            raise ContractError(
                "Copilot, APM and checks can use host files, network and available login details. "
                "Add --allow-host-access to allow this run; policy still applies.",
                code="advisory_consent_required", outcome=Outcome.UNPROVEN,
            )
        selected = Path(contract)
        if not selected.is_absolute():
            selected = (source.root if source else Path.cwd()) / selected
        with prepare_imports(
            Path.cwd(), selected, source=source, planning=planning, limits=limits,
            on_preparation=logger.on_preparation,
            verbose=verbose,
        ) as (imports_root, backend):
            plan = frontend.plan_contract(
                Path(contract), Path.cwd(), harness=harness, model=model, source=source,
                imports_root=imports_root, apm_backend=backend,
            )
            inventory = workspace.inspect_workspace(plan)
            logger.stop_activity()
            if planning:
                logger.render_plan(plan, inventory)
                return
            logger.on_preparation(ImportsSelectedEvent(plan.imported_skills))
            from ..contracts.engine import run_contract

            result = run_contract(plan, logger=logger, allow_advisory=allow_advisory)
            ctx.exit(int(result.outcome))
    except ContractError as exc:
        logger.render_error(exc)
        ctx.exit(int(exc.outcome))
    except KeyboardInterrupt:
        logger.render_error(
            ContractError(
                "Contract command interrupted. Inspect any printed run record before retrying.",
                code="cancelled",
            )
        )
        ctx.exit(int(Outcome.HALTED))
    except OSError as exc:
        logger.render_error(
            ContractError(
                f"Contract filesystem operation failed: {exc}. Inspect permissions and available space.",
                code="filesystem_error",
            )
        )
        ctx.exit(int(Outcome.HALTED))
    finally:
        logger.close()
