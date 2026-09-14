"""Shared command boundary for explicit contracts, never script fallback."""

from pathlib import Path

import click

from apmx.contracts.events import ImportsSelectedEvent
from apmx.contracts.models import ContractSource, ChainResult
from apmx.core.contract_logger import ContractLogger
from apmx.contracts.records import preparation_failure


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
    factory_root: Path | None = None,
    allow_unproven_inputs: bool = False,
) -> ChainResult | None:
    """Plan or execute a local factory or one explicit leaf through shared admission."""
    from ..contracts import frontend, workspace
    from ..contracts.models import ContractError, ContractLimits, Outcome
    from ..install.contract_source import prepare_imports

    if logger is None:
        logger = ContractLogger(verbose=verbose)
    chain_result = None
    try:
        logger.start_activity("Reading contract", announce=False)
        limits = ContractLimits()
        root = Path.cwd().resolve()
        graph = None
        consent_source = "flag"
        if factory_root is not None:
            from ..contracts.resolution import resolve_factory, select_factory_root

            if source is not None:
                raise ContractError(
                    "Package selection supports explicit leaf contracts only.",
                    code="unsupported_factory_source",
                )
            root = select_factory_root(factory_root)
            logger.select_factory_root(root)
        frontend.admit_caller_policy(root, limits=limits)
        selected = Path(contract)
        if not selected.is_absolute():
            selected = (source.root if source else root) / selected
        preparation_target = selected
        if factory_root is not None:
            graph = resolve_factory(root, limits=limits)
            preparation_target = next(
                (item.path for item in graph.order if item.imports),
                graph.order[0].path,
            )
            if (
                not planning
                and not allow_advisory
                and not allow_unproven_inputs
                and logger.can_confirm_factory()
            ):
                logger.render_factory_work(graph)
                if not logger.confirm_factory():
                    raise ContractError(
                        "Factory not started: the local execution profile was not authorized.",
                        code="consent_declined",
                        outcome=Outcome.UNPROVEN,
                    )
                allow_advisory = allow_unproven_inputs = True
                consent_source = "interactive"
                if resolve_factory(root, limits=limits) != graph:
                    raise ContractError(
                        "Factory changed during confirmation. Preview it again.",
                        code="plan_changed",
                    )
        if not planning and not allow_advisory:
            raise ContractError(
                "Copilot, APM and checks can use host files, network and available login details. "
                "Add --allow-host-access to allow this run; policy still applies. "
                "Factories in automation also need --allow-unproven-inputs for native handoffs.",
                code="advisory_consent_required",
                outcome=Outcome.UNPROVEN,
            )
        with prepare_imports(
            root,
            preparation_target,
            source=source,
            planning=planning,
            limits=limits,
            on_preparation=logger.on_preparation,
            verbose=verbose,
        ) as (imports_root, backend):
            if graph is not None:
                from ..contracts.resolution import preflight
                from ..contracts.chain import run_chain

                closure = preflight(
                    graph,
                    root,
                    harness=harness,
                    model=model,
                    source=source,
                    limits=limits,
                    imports_root=imports_root,
                    apm_backend=backend,
                    allow_unproven_inputs=allow_unproven_inputs,
                )
                logger.stop_activity()
                if planning:
                    logger.render_chain_plan(closure)
                    return None
                chain_result = run_chain(
                    closure,
                    logger=logger,
                    allow_advisory=allow_advisory,
                    consent_source=consent_source,
                )
                return chain_result
            plan = frontend.plan_contract(
                Path(contract),
                root,
                harness=harness,
                model=model,
                source=source,
                imports_root=imports_root,
                apm_backend=backend,
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
        error = preparation_failure(chain_result, exc)
        logger.render_error(error)
        ctx.exit(int(error.outcome))
    except KeyboardInterrupt:
        logger.render_error(
            preparation_failure(
                chain_result,
                ContractError(
                    "Contract command interrupted. Inspect any printed run record before retrying.",
                    code="cancelled",
                ),
            )
        )
        ctx.exit(int(Outcome.HALTED))
    except OSError as exc:
        logger.render_error(
            preparation_failure(
                chain_result,
                ContractError(
                    f"Contract filesystem operation failed: {exc}. Inspect permissions and available space.",
                    code="filesystem_error",
                ),
            )
        )
        ctx.exit(int(Outcome.HALTED))
    finally:
        logger.close()
