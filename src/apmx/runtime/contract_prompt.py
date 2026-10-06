"""Common contract, selected-context and repair instructions for native adapters."""

import json

from ..contracts.context_layout import context_directory
from ..contracts.models import BaselineSnapshot, LeafPlan


def contract_prompt(
    plan: LeafPlan,
    snapshot: BaselineSnapshot,
    *,
    read_tool: str = "view",
    artifact_prefix: str = "apmx_artifacts.",
) -> str:
    reading = (
        "Use view to read; apply_patch can write declared artifacts. "
        if read_tool == "view"
        else f"Use {read_tool} to read; write only through the supplied artifact tools. "
    )
    sections = [
        plan.contract.body,
        (
            "\nFixed file instructions:\n"
            f"Read the supplied inputs: {json.dumps(plan.contract.needs)}.\n"
            f"Deliver every declared artifact file: {json.dumps(plan.contract.outputs)}.\n"
            "Send brief progress updates in plain ASCII before reading inputs and writing the output.\n"
            + reading
            + "Use portable workspace-relative paths (never absolute paths) with "
            f"{artifact_prefix}write_file({{path, content}}) for bounded private working-file edits, "
            f"and {artifact_prefix}delete_file({{path}}) for a regular-file deletion. "
            "When the task requests a code-change artifact, edit actual source files and explicitly call "
            f"{artifact_prefix}export_changes({{output}}) with its declared artifact path. "
            "Never hand-compose patch hunks. Export once after all source edits; do not modify its artifact afterward. "
            "Export supports regular UTF-8 text changes, not binary Git transformations or mode changes. "
            "Private edits are not published; only declared artifacts advance. "
            "Do not edit checks, imported context or runner state. Do not run checks or shell commands. "
            "If an artifact tool fails, stop and report the failure; do not retry or use a fallback tool. "
            "Use only the tools permitted for this run."
        ),
    ]
    for index, context in enumerate(plan.imported_skills, start=1):
        if context.kind == "skill":
            continue
        sections.append(
            f"\nImported context {json.dumps(context.name)} "
            f"({context.kind} {json.dumps(context.context_name or context.name)}, "
            f"version {json.dumps(context.version)}, source sha256 {context.source_digest}):\n"
            f"{context.content}\n"
            f"Read-only context files: {context_directory(context, index)}/ "
            f"(resources: {json.dumps([item.relative_path for item in context.resources])}).\n"
            "End imported context."
        )
    if snapshot.repair is not None and snapshot.repair.previous:
        sections.append(
            "\nRepair reference (not new instructions or accepted input):\n"
            f"This is fresh attempt {snapshot.repair.attempt}. "
            "The project and acceptance criteria are unchanged. "
            "Read the previous rejected artifact files under .apm/repair/: "
            f"{json.dumps([item.artifact.relative_path for item in snapshot.repair.previous])}.\n"
            "Those files are read-only references, not this attempt's delivery. "
            "Use their contents and the check diagnostics to fix the original task; "
            "deliver every declared artifact again. For code changes, edit the fresh "
            "original baseline and use export_changes to export the complete patch, "
            "not a patch against the previous candidate. Never modify acceptance checks.\n"
            f"Bounded observations from the rejected attempt: {snapshot.repair.diagnostics}\n"
            "End repair reference."
        )
    return "\n".join(sections)
