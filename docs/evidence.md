# Receipts: portable execution evidence

Every COMPLETE contract, factory or repair controller writes a **receipt** next
to its record (`.apm/runs/<id>/`, `.apm/chains/<id>/` or
`.apm/controllers/<id>/`). It has three parts, each with one job:

| Part | What it is | Who needs it |
| --- | --- | --- |
| `receipt.intoto.jsonl` | One standard in-toto bundle: one DSSE envelope per line, each an in-toto Statement v1 | everyone; `apmx audit` |
| `outputs/` | The delivered files, at their project paths | the user, the PR |
| `evidence/sha256/<hex>` | Content-addressed, deduplicated material the statements name by digest | forensics, `apmx check` replay; optional for verification |

REJECTED and HALTED runs get no receipt: a receipt attests a COMPLETE result
only. Their saved attempt outputs and check logs stay under `.apm/runs/<id>/`.
The export neither runs a model nor reruns checks. Measured on the real
five-stage checkout factory (v0.6.0, Copilot): v1 wrote 232 files (1.3 MB);
v2 writes 1 bundle (37 KB, 11 lines), 4 delivered files and 31 evidence blobs
(188 KB).

## What is inside `receipt.intoto.jsonl`

Locally each envelope has `"payloadType": "application/vnd.in-toto+json"` and
`"signatures": []`, which is honest about being unsigned. In CI the bundle and
a verification summary that binds every line are signed with Sigstore and
stored in GitHub's attestation store (see
[signed deliveries](audit.md#signed-deliveries)). **Every statement names
the same subjects: the delivered files.** For a factory that produces patches
(`git diff --full-index`, as the `export_changes` tool writes), those are the
files the patches produce, applied in stage order to the files the attempt
started from, not the `.diff` itself; otherwise the declared outputs.

| Line | Predicate type | Says |
| --- | --- | --- |
| 1 | `https://slsa.dev/provenance/v1` | built by this factory, from these inputs, by this harness and model |
| 2..n | `https://in-toto.io/attestation/test-result/v0.1`, one per check of the final candidate | check X ran: PASSED |
| last | `https://cyclonedx.org/bom` (CycloneDX 1.5, inline) | the APM packages and skills the agents were given |
| (CI) | `https://slsa.dev/verification_summary/v1` | the verifier checked every other line: PASSED |

Real lines from the v0.6.0 checkout run, shortened:

```json
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "src/pricing.py", "digest": {"sha256": "e6981e53e1ed..."}}, "..."],
 "predicateType": "https://slsa.dev/provenance/v1",
 "predicate": {
  "buildDefinition": {
   "buildType": "https://github.com/danielmeppiel/apmx/build/factory/v2",
   "externalParameters": {"factory": {"package": "danielmeppiel/apmx-demo-factory#v1.0.0",
     "commit": "e8e9c5a7b1f1...", "definition": {"sha256": "0bec0549b9af..."}}},
   "internalParameters": {"stages": [
     {"contract": "build", "contractSha256": "8989452b4474...", "harness": "copilot",
      "harnessVersion": null, "requestedModel": null, "observedModels": ["gpt-6.1-sol"],
      "attempts": 1, "budget": {"max_attempts": 3, "max_seconds": 600},
      "checks": [{"name": "shipping-examples", "command": "python3 -I -B checks/acceptance.py changes.diff"}, "..."],
      "outputs": [{"name": "changes.diff", "digest": {"sha256": "d72aafd228f1..."}}, "..."]}, "..."]},
   "resolvedDependencies": [
     {"name": "base", "digest": {"gitTree": "dabee76cced9..."},
      "annotations": {"apmx.role": "base", "created": ["tests/test_free_shipping.py"], "deleted": []}},
     {"name": "src/pricing.py", "digest": {"sha256": "b11e523c3a2b..."}, "annotations": {"apmx.role": "before-image"}},
     {"name": "request.md", "digest": {"sha256": "1808ed8ffe32..."}, "annotations": {"apmx.role": "input", "evidence": "evidence/sha256/1808ed8ffe32..."}},
     {"name": "python-testing-patterns", "uri": "pkg:generic/agents@46891e7e...", "digest": {"sha256": "<SKILL.md>"}, "annotations": {"apmx.role": "capability", "bomRef": "..."}},
     {"name": "apm.lock.yaml", "digest": {"sha256": "2aa9ff56bb6d..."}, "content": "<base64>", "annotations": {"apmx.role": "apm-lock"}}]},
  "runDetails": {
   "builder": {"id": "https://github.com/danielmeppiel/apmx", "version": {"apmx": "0.7.0", "apm": "0.33.0"}},
   "metadata": {"invocationId": "20261021T184925Z-67361d3ba1d4", "startedOn": "...", "finishedOn": "..."},
   "byproducts": [
     {"name": "definition.json", "digest": {"sha256": "0bec0549b9af..."}, "annotations": {"apmx.role": "definition", "evidence": "evidence/sha256/0bec0549b9af..."}},
     {"name": "transcripts/<run>.log", "digest": {"sha256": "..."}, "annotations": {"apmx.role": "transcript", "evidence": "..."}},
     {"name": "attempts/<run>.json", "digest": {"sha256": "..."}, "annotations": {"apmx.role": "rejected-attempt", "evidence": "..."}}]}}}
```

```json
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "src/pricing.py", "digest": {"sha256": "e6981e53e1ed..."}}, "..."],
 "predicateType": "https://in-toto.io/attestation/test-result/v0.1",
 "predicate": {"result": "PASSED",
  "passedTests": ["Free delivery at 5000 cents", "below", "above", "..."],
  "configuration": [{"name": "checks/acceptance.py", "digest": {"sha256": "8a94940e06d8..."},
    "annotations": {"check": "shipping-examples", "stage": "build",
      "command": "python3 -I -B checks/acceptance.py changes.diff",
      "factoryDefinition": "sha256:0bec0549b9af...",
      "scope": "Observed checks over the reconstructed candidate files; not a correctness guarantee."}}],
  "url": "evidence/sha256/2ddc3521743e..."}}
```

Where each fact lives, and why:

- **Factory definition.** `externalParameters.factory.definition` is the
  SHA-256 of the canonical definition (sorted, compact ASCII JSON with a final
  LF), exactly as in v1, so `factories.allow` pins keep working. The definition
  itself is a byproduct in `evidence/` (`apmx audit --explain factory`). It
  covers exact contract bytes, declared checks, checker resources, selected
  capability content and pins, limits, repair budgets and resolved APM
  dependencies; not harness, model, clocks or paths.
- **Stages.** `internalParameters.stages[]` (SLSA's slot for settings the
  platform chose) records each contract's harness, the version it reported
  (`<harness> --version` at run start; `null` when unknown), the requested
  model, every model it reported across all attempts, the attempt count and
  budget, its declared checks and its outputs by digest. In a receipt of record
  written by `apmx check`, harness and models are the producer's, labelled
  `"reportedBy": "producer"` and `"checkedBy": "apmx check (replay)"`.
- **Base, before-images and deletions** are `resolvedDependencies`: a `base`
  descriptor (the git tree of the files the run started from; in CI also
  `gitCommit` and `uri git+https://github.com/<repo>@<commit>`, from
  `APMX_BASE_REPOSITORY`/`APMX_BASE_COMMIT`) whose annotations list the files
  the delivery `created` and `deleted` (`[{path, beforeDigest}]`), plus one
  `before-image` descriptor per touched file that existed. These are inputs the
  factory read, so `resolvedDependencies` is their standard place; a gate
  compares them with the PR base for freshness.
- **Inputs and capabilities** are `resolvedDependencies` too: caller-provided
  inputs by digest (bytes in `evidence/`), each selected capability by its
  CycloneDX `purl` and `SKILL.md` digest, and the run's `apm.yml` and
  `apm.lock.yaml` inline (`content`), so ingredients can be audited without
  `evidence/`.
- **Checks.** A Test Result's `configuration` is the list of checker files it
  ran, by digest, with the check name, command, stage and definition in
  `annotations`; no separate configuration file. `url` names the raw checker
  report or log in `evidence/`. Generic checks are suite-level observations
  without invented per-test names; the software-factory checker profiles are
  validated against reconstructed candidate files at export.
- **Rejected attempts** become provenance `byproducts`: a record of each, by
  digest, with its outputs, check results and models in `evidence/`.
- **Ingredients.** The official APM CycloneDX 1.5 export is the last line's
  predicate. Without a retained APM lock it lists zero components and says so
  in `metadata.properties` (`apmx:apm-dependencies` = `0`). `apmx audit
  --export abom` writes it as a standalone file.

All digests are SHA-256 over actual bytes. Statements are serialized as
sorted, compact ASCII JSON, and a line's digest (what a verification summary's
`inputAttestations` name) is the SHA-256 of its canonical unsigned line.

## Export failures

With a retained lock, missing or inconsistent inventory, ambiguous capability
bindings, unavailable checked subjects or patches that do not combine are
explicit delivery failures, not a silent downgrade. A run that selected
capabilities without a retained lock refuses export rather than claiming zero
dependencies. The command exits **23** while its canonical execution record
remains COMPLETE; `--plan` never exports. See [execution outcomes](results.md).

One observed APM limitation (0.30.0 through the pinned 0.33.0) is overlapping
direct and transitive local references: the official export can contain
duplicate components with the same `bom-ref`. CycloneDX rejects the duplicate
inventory, and APMX reports delivery failure rather than deduplicating it.

## Export again without inference

Use the development Python environment and, when the run had APM dependencies,
the exact recorded official APM backend. Supply an original completed root
record and a directory without a receipt:

```sh
.venv/bin/python - /absolute/path/to/record.json /absolute/path/to/new-dir <<'PY'
import sys
from pathlib import Path
from apmx.contracts.evidence import export_package

print(export_package(Path(sys.argv[1]), Path(sys.argv[2])))
PY
```

This validates retained evidence, reconstructs checked candidates and applies
patches in owned temporary directories without executing their code, verifies
what it wrote, and publishes by rename. It refuses to overwrite an existing
receipt and never rewrites canonical records. The export has a 512 MiB byte bound.

## Verify a receipt: `apmx audit`

```sh
apmx audit .apm/chains/<id>/receipt.intoto.jsonl --outputs .apm/chains/<id>/outputs
apmx audit --explain stages
```

[`apmx audit`](audit.md) is the primary verifier, locally and as the PR gate.

## Independent, offline verification

The single verifier lives in `src/apmx/audit/verifier.py`. It imports only the
standard library and pinned upstream in-toto protobuf bindings, protobuf and
jsonschema. `scripts/verify_evidence.py` is a thin wrapper that loads it **by
file path**, so it does **not** import APMX. From the source checkout:

```sh
python3 -m venv .venv-evidence
.venv-evidence/bin/python -m pip install -r scripts/evidence-requirements.txt
.venv-evidence/bin/python scripts/verify_evidence.py /path/to/run-dir --require-evidence
.venv-evidence/bin/python scripts/check_evidence_controls.py /path/to/run-dir \
  --schemas src/apmx/audit/schemas
```

Validation is offline and uses the hash-pinned CycloneDX schemas bundled in
`src/apmx/audit/schemas/` unless you pass `--schemas DIR` (add
`--fetch-schemas` to download the same pinned bytes). `--require-capability`
is a demonstration gate for an actual selected capability. The controls script
verifies a relocated copy, then rejects copies with corrupted evidence, a
non-canonical line, a wrong predicate, a moved subject, an invalid result, a
missing builder, a changed stage or an omitted check, each re-encoded so it
cannot pass by testing only a stale checksum. Originals are unchanged.

## What this does not guarantee

Local receipts are **unsigned, same-user observations**. They provide
inspectability and content binding, not an authenticated signer, an SLSA
level, a sandbox, an independent witness, general software correctness or
permission to merge or deploy. A person who can rewrite every file and hash can
forge a self-consistent receipt; a summary signed in CI by an allowed workflow
is what says who produced it.

`evidence/` includes transcripts, inputs, intermediate stage outputs and the
files the run started from, and the bundle carries the APM manifest and lock.
They can contain private code, paths or secrets. **Review before sharing**;
`evidence/` never belongs in a pull request.
