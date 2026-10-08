"""Shared command boundary for explicit contracts, never script fallback."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import click

from apmx.contracts.events import ImportsSelectedEvent
from apmx.contracts.models import ChainResult, ContractSource, RunResult
from apmx.contracts.records import CompletionBoundary, preparation_failure
from apmx.core.contract_logger import ContractLogger


def invoke_contract(
    ctx: click.Context,
    contract: str,
    *,
    harness: str,
    model: str | None,
    verbose: bool,
    planning: bool,
    completion: CompletionBoundary,
    allow_advisory: bool = False,
    source: ContractSource | None = None,
    logger: ContractLogger | None = None,
    factory_root: Path | None = None,
    allow_unproven_inputs: bool = False,
    tui: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
) -> ChainResult | RunResult | None:
    """Plan or execute a local factory or one explicit leaf through shared admission."""
    from ..contracts import frontend, workspace
    from ..contracts.models import ContractError, ContractLimits, Outcome
    from ..install.contract_source import prepare_imports

    if logger is None:
        logger = ContractLogger(verbose=verbose)
    if not planning and factory_root is None:
        logger.execution_context()
    chain_result = None
    try:
        logger.start_activity("Reading contract", announce=False)
        limits = ContractLimits()
        root = Path.cwd().resolve()
        graph = None
        consent_source = "flag"
        interactive_workspace = (
            logger.presentation is not None
            and not planning
            and not allow_advisory
            and not allow_unproven_inputs
            and logger.can_confirm_factory()
        )
        if factory_root is not None:
            from ..contracts.resolution import resolve_factory, select_factory_root

            if source is None:
                root = select_factory_root(factory_root)
            logger.select_factory_root(root)
        frontend.admit_caller_policy(root, limits=limits)
        selected = Path(contract)
        if not selected.is_absolute():
            selected = (source.root if source else root) / selected
        preparation_target = selected
        if factory_root is not None:
            graph = resolve_factory(factory_root, caller=root, limits=limits)
            preparation_target = next(
                (item.path for item in graph.order if item.imports),
                graph.order[0].path,
            )
            if interactive_workspace and source is None:
                logger.render_factory_work(graph, project_root=root, harness=harness)
                if not logger.confirm_package_preparation(str(factory_root)):
                    raise ContractError(
                        "Factory preparation was declined; nothing executed.",
                        code="consent_declined",
                        outcome=Outcome.UNPROVEN,
                    )
            if (
                not planning
                and not allow_advisory
                and not allow_unproven_inputs
                and logger.can_confirm_factory()
                and logger.presentation is None
            ):
                logger.render_factory_work(graph, project_root=root, harness=harness)
                if not logger.confirm_factory():
                    raise ContractError(
                        "Factory not started: the local execution profile was not authorized.",
                        code="consent_declined",
                        outcome=Outcome.UNPROVEN,
                    )
                allow_advisory = allow_unproven_inputs = True
                consent_source = "interactive"
                if resolve_factory(factory_root, caller=root, limits=limits) != graph:
                    raise ContractError(
                        "Factory changed during confirmation. Preview it again.",
                        code="plan_changed",
                    )
            if not planning:
                logger.execution_context(factory=True)
        if not planning and not allow_advisory and not interactive_workspace:
            raise ContractError(
                f"{ContractLogger._harness_label(harness)}, APM and checks can use host files, "
                "network and available login details. "
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
            **({"cancel_requested": cancel_requested} if cancel_requested else {}),
        ) as (imports_root, backend):
            if graph is not None:
                from ..contracts.chain import run_chain
                from ..contracts.resolution import preflight

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
                if logger.presentation is not None:
                    logger.render_factory_work(closure.graph, project_root=root, harness=harness)
                    if planning:
                        logger.render_chain_plan(closure)
                        return None
                    if interactive_workspace:
                        if not logger.confirm_factory():
                            raise ContractError(
                                "Factory execution was declined; no agent or check ran.",
                                code="consent_declined",
                                outcome=Outcome.UNPROVEN,
                            )
                        allow_advisory = allow_unproven_inputs = True
                        closure = replace(closure, allow_unproven_inputs=True)
                        consent_source = "interactive"
                        if resolve_factory(factory_root, caller=root, limits=limits) != graph:
                            raise ContractError(
                                "Factory changed during confirmation. Preview it again.",
                                code="plan_changed",
                            )
                    if cancel_requested is not None and cancel_requested():
                        raise ContractError("Factory cancelled before execution.", code="cancelled")
                    logger.presentation.phase("Run")
                if planning:
                    if tui:
                        from ..tui.entry import launch_preview, tui_eligible

                        if not tui_eligible():
                            raise ContractError(
                                "The TUI preview needs an interactive terminal on both ends; "
                                "omit --tui or redirect/CI runs stay on --plan.",
                                code="tui_unavailable",
                                outcome=Outcome.UNPROVEN,
                            )
                        launch_preview(closure.graph)
                        return None
                    logger.render_chain_plan(closure)
                    return None
                if tui:
                    from ..tui.entry import tui_eligible

                    if not tui_eligible():
                        raise ContractError(
                            "The live TUI needs an interactive terminal on both ends; "
                            "omit --tui for a normal run.",
                            code="tui_unavailable",
                            outcome=Outcome.UNPROVEN,
                        )
                    from .tui_live import launch_live

                    chain_result = launch_live(
                        closure,
                        logger=logger,
                        allow_advisory=allow_advisory,
                        consent_source=consent_source,
                    )
                else:
                    chain_result = run_chain(
                        closure,
                        logger=logger,
                        allow_advisory=allow_advisory,
                        consent_source=consent_source,
                        cancel_requested=cancel_requested,
                    )
                if logger.presentation is not None:
                    logger.presentation.phase("Finalizing")
                    logger.presentation.result(chain_result)
                completion.capture(chain_result)
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
            if tui:
                raise ContractError(
                    "The TUI needs a factory directory; pass a factory directory, not "
                    "a single contract file.",
                    code="tui_requires_factory",
                    outcome=Outcome.UNPROVEN,
                )
            if planning:
                logger.render_plan(plan, inventory)
                return
            logger.on_preparation(ImportsSelectedEvent(plan.imported_skills))
            from ..contracts.engine import run_contract

            chain_result = run_contract(
                plan, logger=logger, allow_advisory=allow_advisory, announce_result=False
            )
            completion.capture(chain_result)
            return chain_result
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
