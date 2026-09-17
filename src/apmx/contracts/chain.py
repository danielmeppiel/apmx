"""One admitted graph, sequential leaf-engine calls, and exact retained handoffs."""

from dataclasses import replace

from ..core.contract_logger import ContractLogger
from . import engine, frontend, records, resolution, workspace
from .events import ImportsSelectedEvent
from .models import ChainResult, ContractError, LeafPlan, Outcome, RetainedInput
from .resolution import ChainPlan, Node


def describe(plan: ChainPlan) -> dict:
    root = plan.graph.root
    return {
        "root": str(root),
        "targets": [target.relative_to(root).as_posix() for target in plan.graph.targets],
        "catalog": [
            {"contract": item.path.relative_to(root).as_posix(), "sha256": item.source_digest}
            for item in plan.graph.catalog
        ],
        "order": [
            {
                "contract": node.plan.contract.path.relative_to(root).as_posix(),
                "needs": node.plan.contract.needs,
                "produces": node.plan.contract.produces,
                "checks": [check.name for check in node.plan.contract.checks],
            }
            for node in plan.nodes
        ],
        "edges": [
            {
                "from": edge.producer.relative_to(root).as_posix(),
                "to": edge.consumer.relative_to(root).as_posix(),
                "input": edge.name,
            }
            for edge in plan.graph.edges
        ],
    }


def _current(node: Node) -> LeafPlan:
    plan = node.plan
    current = frontend.plan_contract(
        plan.contract.path,
        plan.project_root,
        harness=plan.harness,
        model=plan.model,
        limits=plan.limits,
        source=plan.source,
        imports_root=plan.imports_root,
        apm_backend=plan.apm_backend,
        deferred_inputs=plan.deferred_inputs,
        chain_outputs=plan.chain_outputs,
    )
    if current != plan or workspace.inspect_workspace(current) != node.inventory:
        raise ContractError(
            "Admitted graph inputs, resources or prerequisites changed. Plan again.",
            code="plan_changed",
        )
    return current


def _revalidate(plan: ChainPlan) -> None:
    limits = plan.nodes[0].plan.limits
    if resolution.discover(plan.graph.root, limits) != plan.graph.catalog:
        raise ContractError("Admitted contract catalog changed. Plan again.", code="plan_changed")
    for node in plan.nodes:
        _current(node)


def _bound(node: Node, admitted: dict[str, RetainedInput]) -> LeafPlan:
    bindings = tuple(admitted[name] for name in node.plan.deferred_inputs)
    plan = replace(node.plan, deferred_inputs=(), input_bindings=bindings)
    inventory = workspace.inspect_workspace(plan)
    remaining = tuple(
        item for item in inventory if item.relative_path not in node.plan.deferred_inputs
    )
    if remaining != node.inventory:
        raise ContractError("Original caller inputs/resources changed.", code="plan_changed")
    return replace(plan, input_inventory=inventory)


def run_chain(
    plan: ChainPlan,
    *,
    logger: ContractLogger,
    allow_advisory: bool = False,
    consent_source: str = "flag",
) -> ChainResult:
    """Run each inferred node once; only the record authority admits downstream bytes."""
    logger.execution_context()
    if not allow_advisory:
        raise ContractError(
            "Native execution requires --allow-host-access; use --plan to preview.",
            code="advisory_consent_required",
            outcome=Outcome.UNPROVEN,
        )
    _revalidate(plan)
    store = records.ChainStore.create_chain(
        plan.nodes[0].plan.project_root,
        describe(plan),
        allow_unproven=plan.allow_unproven_inputs,
        allow_host_access=allow_advisory,
        consent_source=consent_source,
    )
    states = [
        {"contract": item["contract"], "state": "pending", "result": None}
        for item in describe(plan)["order"]
    ]
    runs = []
    admitted: dict[str, RetainedInput] = {}
    finalized: list[RetainedInput] = []
    completed = []
    outcome = Outcome.UNPROVEN
    stop_reason = None
    complete = False
    current = 0
    catalog = tuple(node.plan.contract.path for node in plan.nodes)
    try:
        logger.attach_run(store.run_id, store.directory)
        store.update("execution", nodes=states)
        for current, node in enumerate(plan.nodes):
            states[current]["state"] = "running"
            store.update("execution", nodes=states)
            executable = _bound(node, admitted)
            logger.chain_node(
                current + 1, len(plan.nodes), node.plan.contract.path, catalog=catalog
            )
            leaf_logger = logger.new_leaf(
                index=current + 1,
                count=len(plan.nodes),
                contract=node.plan.contract.path,
            )
            try:
                leaf_logger.on_preparation(ImportsSelectedEvent(executable.imported_skills))
                result = engine.run_contract(
                    executable,
                    logger=leaf_logger,
                    allow_advisory=allow_advisory,
                    consent_source=consent_source,
                    allow_unproven_inputs=plan.allow_unproven_inputs,
                )
            finally:
                leaf_logger.close()
            runs.append(result)
            states[current]["result"] = result
            _revalidate(plan)
            if any(edge.producer == node.plan.contract.path for edge in plan.graph.edges):
                if isinstance(node.plan.contract.produces, str):
                    bindings = (
                        records.admit_handoff(
                            executable,
                            result,
                            allow_unproven=plan.allow_unproven_inputs,
                        ),
                    )
                else:
                    bindings = records.admit_handoffs(
                        executable,
                        result,
                        allow_unproven=plan.allow_unproven_inputs,
                    )
                admitted.update((binding.artifact.relative_path, binding) for binding in bindings)
                states[current]["input_exception"] = records.native_assurance_limited(result)
            else:
                bindings = (
                    (records.finalized_input(executable, result),)
                    if isinstance(node.plan.contract.produces, str)
                    else records.finalized_inputs(executable, result)
                )
            finalized.extend(bindings)
            completed.append((executable, result))
            states[current]["record_sha256"] = bindings[0].record_sha256
            states[current]["state"] = "completed"
            store.update("execution", nodes=states)
        for binding in finalized:
            records.validate_binding(
                binding, plan.nodes[0].plan.project_root, plan.nodes[0].plan.limits
            )
        view = workspace.capture_chain_view(store.directory, tuple(completed), tuple(finalized))
        store.update("artifacts", artifacts=view, nodes=states)
        complete = True
        outcome = Outcome.COMPLETE
    except (ContractError, OSError, KeyboardInterrupt) as exc:
        outcome = exc.outcome if isinstance(exc, ContractError) else Outcome.HALTED
        stop_reason = (
            exc.code
            if isinstance(exc, ContractError)
            else ("cancelled" if isinstance(exc, KeyboardInterrupt) else "filesystem_error")
        )
        states[current]["state"] = "stopped"
        states[current]["reason"] = (
            str(exc) or "Interrupted; inspect retained evidence and processes."
        )
        for state in states[current + 1 :]:
            state.update(state="blocked", reason=stop_reason)
        logger.chain_stopped(states[current]["reason"], outcome=outcome, code=stop_reason)
    result = ChainResult(
        store.run_id, store.record_path, outcome, complete, tuple(runs), stop_reason
    )
    try:
        logger.close()
        store.update("record", transcript_retention=logger.transcript_metadata)
    except (OSError, KeyboardInterrupt) as exc:
        store.fail_finalization(result, exc)
    store.finish_chain(result, states)
    return result
