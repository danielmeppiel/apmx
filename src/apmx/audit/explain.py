"""``apmx audit --explain`` and ``--export abom``: read a verified receipt for people and tools.

These replace v1's ``summary.md`` and standalone ``abom.cdx.json``: they are
generated on demand from the bundle (and its evidence, when present), only
after the bundle verified structurally.
"""

from __future__ import annotations

import json

from . import stages, verifier

TOPICS = ("factory", "stages", "checks", "dependencies", "outputs")


def _ascii(text: object) -> str:
    return "".join(char if " " <= char <= "~" else "?" for char in str(text))


def _short(digest: object) -> str:
    return f"sha256:{str(digest)[:12]}.." if digest else "-"


def _factory(receipt: verifier.Receipt) -> list[str]:
    predicate = receipt.provenance["predicate"]
    build, details = predicate["buildDefinition"], predicate["runDetails"]
    factory = build["externalParameters"]["factory"]
    metadata = details.get("metadata", {})
    lines = [
        "Factory",
        f"  definition   sha256:{receipt.definition_sha256}",
        f"  standards    {verifier.standards(receipt)}: schema-valid",
        f"  package      {factory['package'] or '(local factory directory)'}",
        f"  commit       {factory['commit'] or '-'}",
        f"  builder      {details['builder']['id']}  "
        + " ".join(f"{k} {v}" for k, v in sorted(details["builder"].get("version", {}).items())),
        (
            f"  invocation   {metadata.get('invocationId', '-')}  "
            f"{metadata.get('startedOn', '-')} .. {metadata.get('finishedOn', '-')}"
        ),
    ]
    base = receipt.base
    lines.append(
        f"  base         {base.get('uri') or '(local)'}  gitTree {base.get('gitTree') or '-'}"
    )
    inputs = [
        item
        for item in build["resolvedDependencies"]
        if verifier.role(item) in ("input", "producer-receipt")
    ]
    for item in inputs:
        lines.append(f"  input        {item['name']}  {_short(item['digest'].get('sha256'))}")
    if receipt.definition is None:
        lines.append("  (definition bytes not present: evidence/ was not shipped)")
    else:
        resources = {
            row["relative_path"]
            for stage in receipt.definition["stages"]
            for row in stage["resources"]
        }
        lines.append(
            f"  defines      {len(receipt.definition['stages'])} contracts, "
            f"{len(resources)} checker resources, "
            f"{len(receipt.definition.get('resolvedDependencies', []))} APM dependencies"
        )
    rejected = [
        item for item in details.get("byproducts", []) if verifier.role(item) == "rejected-attempt"
    ]
    if rejected:
        lines.append(f"  rejected     {len(rejected)} earlier attempt(s), in evidence/")
    return lines


def _stages(receipt: verifier.Receipt) -> list[str]:
    lines = ["Stages"]
    for stage in stages.factory_stages(receipt):
        version = f" {stage['harnessVersion']}" if stage["harnessVersion"] else ""
        models = ", ".join(stage["observedModels"]) or "none reported"
        lines.append(
            f"  {stage['contract']:<16} {stage['harness']}{version}  "
            f"requested {stage['requestedModel'] or 'default'}  observed {models}  "
            f"attempts {stage['attempts']}"
        )
        if stage.get("reportedBy"):
            lines.append(
                f"  {'':<16} harness and models reported by the {stage['reportedBy']}; "
                f"checks rerun by {stage.get('checkedBy', 'apmx check')}"
            )
    return lines


def _checks(receipt: verifier.Receipt) -> list[str]:
    lines = ["Checks"]
    for test in receipt.tests:
        predicate = test["predicate"]
        annotations = predicate["configuration"][0].get("annotations", {})
        cases = len(predicate.get("passedTests", []))
        suffix = f"  {cases} reported case(s)" if cases else ""
        lines.append(
            f"  [{predicate['result']}] {annotations.get('stage')}/{annotations.get('check')}"
            f"{suffix}"
        )
        lines.append(f"      {annotations.get('command')}")
        if predicate.get("url"):
            lines.append(f"      log {predicate['url']}")
    return lines


def _dependencies(receipt: verifier.Receipt) -> list[str]:
    lines = ["Dependencies"]
    for binding in receipt.bindings:
        lines.append(
            f"  capability   {binding['capability']}  {binding['purl']}  "
            f"body {_short(binding['bodySha256'])}"
        )
    for component in receipt.bom.get("components", []):
        lines.append(f"  component    {component.get('name')}  {component.get('purl', '-')}")
    for name in sorted(receipt.apm_files):
        lines.append(f"  retained     {name}  {_short(verifier.sha256(receipt.apm_files[name]))}")
    if len(lines) == 1:
        lines.append("  no APM dependencies were installed or selected")
    return lines


def _outputs(receipt: verifier.Receipt) -> list[str]:
    lines = ["Outputs"]
    for name, digest in sorted(receipt.subjects.items()):
        state = "created" if receipt.before.get(name) is None else "modified"
        lines.append(f"  {state:<9} {name}  {_short(digest)}")
    for name, digest in sorted(receipt.deleted.items()):
        lines.append(f"  deleted   {name}  (was {_short(digest)})")
    return lines


def explain(receipt: verifier.Receipt, topic: str = "all") -> str:
    sections = {
        "factory": _factory,
        "stages": _stages,
        "checks": _checks,
        "dependencies": _dependencies,
        "outputs": _outputs,
    }
    chosen = TOPICS if topic == "all" else (topic,)
    blocks = ["\n".join(_ascii(line) for line in sections[name](receipt)) for name in chosen]
    return "\n\n".join(blocks)


def export_abom(receipt: verifier.Receipt) -> bytes:
    """The standalone CycloneDX 1.5 document carried inline by the bundle."""
    return (json.dumps(receipt.bom, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "ascii"
    )
