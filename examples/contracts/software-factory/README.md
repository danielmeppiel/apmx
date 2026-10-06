# Run a checkout feature factory

Give a native producer a checkout feature request. Get a plan, specification,
code and documentation patches, two change reports and an advisory review,
with independent checks between
contracts. APMX follows artifact dependencies; there is no separate pipeline
script or phase-name convention.

The seed application charges 500 cents for delivery. The requested change makes
delivery free from a 5000-cent subtotal while preserving the fee below it.
Both pricing and checkout totals must agree.

## Set up

This is the **development five-stage example**. It requires the corresponding
APMX source checkout with implicit project capture, backend provisioning and
the `factory` extra. Existing v0.4.2 downloads and historical recordings below
contain the older four-stage example, not this implementation.
Use the [source environment instructions](../../../docs/install.md#run-the-current-source-checkout)
with this development checkout selected; do not replace it with an older
pinned checkout. The native harness and checker tools are separate prerequisites.

This directory is the unpublished `checkout-factory` **0.1.0-dev** APM source
package. The separate [checkout project](../checkout-project/request.md) owns
the request, application, existing tests and initial documentation. The build
stage selects the maintained
[`python-testing-patterns`](https://github.com/wshobson/agents/tree/46891e7e60da0e52baf1050b7b6391b64e84c6d9/plugins/python-development/skills/python-testing-patterns)
capability at that exact commit. Its upstream semantic version is unknown;
the native lock records the immutable commit and content hash instead.
The capability supplies test-design guidance, not acceptance rules or permission
to install pytest: this fixture still requires the standard `unittest` framework.
No upstream skill content is copied into this factory package.

Stay in that terminal: `APMX_SOURCE` identifies the pinned example checkout,
and PATH selects this checkout's APMX plus the checker environment.
**Behave 1.3.3 is optional
for APMX**, but required in Python 3.12 for this example's Gherkin checks.

On macOS or Linux, check the selected tools before copying anything:

```sh
command -v apmx
python3 -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
git --version
copilot --version
```

Both APMX and Python must come from the development checkout's `.venv/bin/`.
Behave must report `1.3.3`. The commands below demonstrate Copilot, which must be installed
and authenticated through its own CLI; a version response does not establish
login. Stop on any missing tool.

Create a fresh caller outside Git and copy the example. This uses a unique
temporary directory rather than overwriting earlier runs:

```sh
demo="$(mktemp -d "${TMPDIR:-/tmp}/apmx-demo.XXXXXX")" &&
if git -C "$demo" rev-parse --show-toplevel >/dev/null 2>&1; then
  printf '%s\n' "Stop: choose a demo location outside any Git repository." >&2
  false
else
  mkdir "$demo/feature-factory" &&
  cp -R "$APMX_SOURCE/examples/contracts/software-factory/." "$demo/feature-factory/" &&
  cp -R "$APMX_SOURCE/examples/contracts/checkout-project/." "$demo/feature-factory/" &&
  APM_NO_SCRIPTS=1 "$APMX_SOURCE/dist/apm-backend/apm" install \
    --root "$demo/feature-factory" --only apm --target agent-skills --no-trust-bin &&
  cd "$demo" &&
  printf 'Demo directory: %s\n' "$PWD"
fi
```

Keep the printed directory: outputs and records will live below it, not in
your source installation. Temporary folders can be cleaned by your operating
system; preserve the complete demo directory somewhere durable when finished.
If your chosen location is inside Git, choose another location; never remove
a real project's remotes or policy to bypass admission.
This setup uses the checksum-verified source backend to install the pinned
capability into the disposable example, so the subsequent offline preview can
resolve it. It can access the network and native APM configuration; it makes
no model calls. `APM_NO_SCRIPTS` applies only to that APM child, not the later
APMX invocation. Never set it globally to bypass check execution.

### Windows

Complete the [Windows source environment setup](../../../docs/install.md#windows-source-installation)
for this development checkout.
In that same PowerShell session, make a new caller and adjust only the disposable
contract copies to use `python` from the selected checker environment
(`.venv\Scripts` for this source installation).
A separate `py -3.12` launcher could select an environment without Behave, so
it is not used here.

```powershell
$demo = Join-Path $env:TEMP ("apmx-demo-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $demo -ErrorAction Stop | Out-Null
git -C $demo rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -eq 0) { throw "Choose a demo location outside any Git repository." }
$factory = Join-Path $demo "feature-factory"
New-Item -ItemType Directory -Path $factory -ErrorAction Stop | Out-Null
Copy-Item "$(Join-Path $env:APMX_SOURCE 'examples\contracts\software-factory')\*" $factory -Recurse -ErrorAction Stop
Copy-Item "$(Join-Path $env:APMX_SOURCE 'examples\contracts\checkout-project')\*" $factory -Recurse -ErrorAction Stop
Get-ChildItem (Join-Path $factory "contracts") -Filter "*.contract.md" | ForEach-Object {
  $text = [IO.File]::ReadAllText($_.FullName).Replace("python3 -I -B checks/", "python -I -B checks/")
  [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
}
$previousNoScripts = $env:APM_NO_SCRIPTS
try {
  $env:APM_NO_SCRIPTS = "1"
  & "$env:APMX_SOURCE\dist\apm-backend\apm.exe" install --root $factory --only apm --target agent-skills --no-trust-bin
  if ($LASTEXITCODE -ne 0) { throw "Capability preparation failed." }
} finally {
  $env:APM_NO_SCRIPTS = $previousNoScripts
}
Set-Location $demo
Write-Output "Demo directory: $demo"
```

Keep the selected environment on PATH. The original checkout and check resources
are unchanged. The remaining APMX commands work in PowerShell too; use `python`
instead of `python3` for manual checker replay.

### Use the package from a separate consumer

On macOS/Linux, a source-only development consumer can keep the factory outside
its application tree instead of composing the local example:

```sh
factory_package="$APMX_SOURCE/examples/contracts/software-factory"
consumer="$(mktemp -d "${TMPDIR:-/tmp}/apmx-consumer.XXXXXX")" &&
if git -C "$consumer" rev-parse --show-toplevel >/dev/null 2>&1; then
  printf '%s\n' "Stop: choose a consumer outside any Git repository." >&2
  false
else
  cp -R "$APMX_SOURCE/examples/contracts/checkout-project/." "$consumer/" &&
  APM_NO_SCRIPTS=1 "$APMX_SOURCE/dist/apm-backend/apm" install "$factory_package" \
    --root "$consumer" --only apm --target agent-skills --no-trust-bin &&
  cd "$consumer" &&
  apmx --from "$factory_package" . --on copilot --plan
fi
```

Confirm this new location is outside Git just as in the local setup, and stop
if preparation or preview fails. Then run the same source selection:

```sh
apmx --from "$factory_package" . --on copilot \
  --allow-host-access --allow-unproven-inputs
```

Here `.` selects all contracts at the package root, **not** the consumer's
working directory as a contract catalog. The consumer remains the implicit
workspace and evidence root. APMX prepares one dependency graph using any
consumer choices; only the build stage receives the selected testing skill.
A focused run selects `contracts/build.contract.md` from this same package
after supplying its required upstream documents in the consumer.

The local package and its source checkout must remain available throughout
these development runs. This is not a published source release or an archive
demonstration. The pinned APM 0.30.0 archive formats omit or reject these
independent contract/check resources; **archive acceptance remains blocked**.
Do not wrap them in a dummy skill or copy archive members manually.

## What the contracts deliver

```text
plan -> specification -> code -> documentation -> advisory review
```

The consumer project is the workspace by default: source and documentation
do not need individual entries or a `workspace` field. Each `needs` list names
explicit inputs and stage handoffs. Several inputs from the same producer
cause one producer execution, not several. APMX retains the original eligible
project files, then adds validated handoffs; private producer edits do not
replace that baseline.
See [workspace capture](../../../docs/workspace.md) for ignore rules, bounds and
exclusions. Capture is not a secret scanner or native-host isolation.

| Contract | Published artifact(s) | Independent verification |
| --- | --- | --- |
| [Planning](contracts/planning.contract.md) | `plan.md` | Unique sections, typed targets, complete supplied case references |
| [Specification](contracts/specification.contract.md) | `specification.md` | Unique sections, captured public interfaces, complete case references |
| [Implementation](contracts/build.contract.md) | `changes.diff`, `implementation.md` | Apply code patch; run fixed Gherkin/regressions; check report format |
| [Documentation](contracts/documentation.contract.md) | `documentation.diff`, `documentation.md` | Compose both patches; check real docs, links and executable examples; rerun fixed suites; check report format |
| [Advisory review](contracts/review.contract.md) | `review.md` | Typed findings referring to existing artifact lines; zero findings is valid |

The [versioned example formats](checks/document-formats.md) keep reports readable
and references machine-checkable. They are not a universal APMX schema.
Document checks assess format/reference consistency, not prose truth or review quality.
The build alone declares a [bounded repair budget](../../../docs/repair.md):
at most three attempts sharing 600 seconds for native execution and checks.
Planning, specification, documentation and review each remain one attempt.
These are execution limits, not model-latency or spending guarantees.
The planning check retains its public `plan-sections` key for compatibility;
in this development version it checks the full format and typed references,
not just headings. Historical records retain their original, narrower scope.
The [request](../checkout-project/request.md) and [supplied acceptance](checks/features/free-shipping.feature)
remain authoritative. Producers are not asked to claim that they ran checks.

The implementation contract publishes files, not source-directory write scopes:

```yaml
produces:
  - changes.diff
  - implementation.md
verify:
  shipping-examples: python3 -I -B checks/acceptance.py changes.diff
  checkout-regression: python3 -I -B checks/regression.py changes.diff
  implementation-report-format: python3 -I -B checks/documents.py implementation implementation.md
```

The producer edits its private source copies and uses the runtime's bounded Git
export tool to create the patch. It also writes the Markdown report. APMX
publishes those two declared artifacts, not the whole working directory.
The caller's source files are never overwritten.

Documentation is a separate delivery: the next producer updates the actual
`docs/checkout.md` in the consumer, exports a **docs-only** `documentation.diff` and writes a
change report. It does not alter `changes.diff`. Final delivery is the pair
of patches, applied **code first, documentation second** to the original
project. Check evidence names both patch hashes, the accepted code-only tree
and the combined candidate tree. A report alone does not count as updated docs.

The documentation checker verifies the complete typed example table against
the supplied case inventory and executes those same cases through the patched
APIs. It checks inline local links and H2 anchors but does not fetch external
URLs or execute generated code fences. Another check reruns fixed Gherkin and
both regression suites on the combined tree. These finite observations do not
certify arbitrary prose semantics.

The supplied feature includes this concrete threshold scenario:

```gherkin
Scenario: Free delivery at 5000 cents
  Given a subtotal of 5000
  When I request pricing and checkout
  Then delivery is 0 cents and the total is 5000 cents
```

Additional examples cover 4999, 5001, zero, a large subtotal, negatives, booleans,
a float, a string, null, a list and an object. Trusted steps call the actual
patched application. Generated unittest tests supplement, not replace, these
examples.

## Run and inspect

From the directory containing `feature-factory`, preview without model work:

```sh
apmx ./feature-factory --on copilot --plan
```

Expect five contracts in dependency order: planning, specification, build,
documentation and review; seven declared output files; and nine checks. Preview exits `0` and does
not install packages, run checkers or verify Copilot login. Continue only when
it succeeds and the plan matches the example.

**Only run trusted contracts, inputs and checkers.** Copilot, checks and package
preparation execute on your host, not in a sandbox; they can use host files,
network and available logins. Model usage can cost money. To execute:

```sh
apmx ./feature-factory --on copilot
```

APMX requests host-access and local-handoff consent. This retains your configured
Copilot model unless you explicitly select another model. APMX runs checks after
each delivery; a failed or incomplete delivery does not advance.

### Automation

Automation must supply consent explicitly:

```sh
apmx ./feature-factory --on copilot \
  --allow-host-access --allow-unproven-inputs
```

In PowerShell, put the same command and both flags on one line.
The second permission admits only complete local deliveries whose required
checks passed. It does not permit failed or incomplete outputs.

### Artifacts and checks

Use the printed aggregate record and `Artifacts:` directory, relative to the
caller directory you created:

```text
feature-factory/.apm/chains/<id>/record.json
feature-factory/.apm/chains/<id>/artifacts/
feature-factory/.apm/runs/<run-id>/record.json
feature-factory/.apm/runs/<run-id>/transcript.log
```

The artifact view contains the exact admitted outputs and original required
inputs/check resources. Do not edit retained records or artifacts. A directory
existing, a green line or exit `21` is not proof of completion. Open the chain
record and confirm `complete: true`, all five `nodes` are `completed`, no
`result.stop_reason`, and all nine checks have `normalized: 0` in the node
results/per-run records. The check names distinguish report format/reference
checks from `shipping-examples`, `checkout-regression`,
`documentation-format-links-examples` and `documented-checkout`.

The completed artifact view must contain all seven deliveries:
`plan.md`, `specification.md`, `changes.diff`, `implementation.md`,
`documentation.diff`, `documentation.md` and `review.md`.
Read the documents and review both patches yourself. The original source and
documentation remain unchanged; APMX does not automatically apply patches to a project.

Replay either patch-aware checker from that artifact view:

```sh
printf 'Paste the printed Artifacts directory: '
IFS= read -r artifacts
if cd "$artifacts"; then
  python3 -I -B checks/acceptance.py changes.diff
  printf 'Acceptance exit: %s\n' "$?"
  python3 -I -B checks/regression.py changes.diff
  printf 'Regression exit: %s\n' "$?"
  python3 -I -B checks/documentation.py changes.diff documentation.diff
  printf 'Documentation exit: %s\n' "$?"
  python3 -I -B checks/documented_checkout.py changes.diff documentation.diff
  printf 'Combined-candidate exit: %s\n' "$?"
fi
```

**Each invocation starts from the captured baseline, applies the exact patch,
and tests that candidate.** A separate `git apply --check` followed by an
unrelated test invocation would not test the same workspace.

On PowerShell, use `Set-Location (Read-Host "Paste the printed Artifacts directory")`
and the same checker commands with `python`. Inspect each check's JSON and exit
status (`$?` immediately after a check in a POSIX shell, `$LASTEXITCODE` in
PowerShell); do not let a later invocation hide an earlier failure.

The regression command runs the supplied cases and original tests in one fresh
Python process, then generated tests in another. Generated-test imports and mocks
cannot alter the original suite's observed results. Both suites must complete;
passing generated tests never cancels an original regression failure.

Each checker prints bounded JSON with baseline, patch, candidate and check
resource hashes, required example identities and observed statuses. APMX
retains check stdout with its ordinary evidence; the example does not invent
runtime records. The checker removes its own private reconstruction directory.
Its Git apply commands enable native Windows long paths for that child process
only; they do not change global Git configuration or normalize captured bytes.

Checker exit codes:

- `0`: every required observation completed and passed within that check's scope.
- `1`: document format/references, application behavior or a test assertion failed.
- `2`: tooling, inputs, patch application or execution was invalid/incomplete.

Empty, filtered, skipped, undefined or pending Gherkin does not pass. External
Behave configuration and environment selection are ignored. Missing optional
Behave returns `2` with setup guidance, not a passing skipped check.

## Recover after a failed or incomplete run

The build automatically retries only eligible candidate rejections within its
declared budget, retaining every attempt and keeping the original goal, project,
capability and checks fixed. Operational failures stop without retrying.
Exhausted or unchanged rejected candidates stop dependent work. The other four
stages remain single-attempt, and APMX does not resume a stopped factory.
A stopped factory may have per-run artifacts but no completed aggregate
`artifacts/` view.

1. **Find the first stopped contract.** Open the printed chain `record.json`.
   Read `result.stop_reason`, the stopped node's `reason` and `result`, and its
   referenced run directory. In that run's `record.json`, inspect required
   `checks`, their `normalized` values and raw `process` observations. Read the
   adjacent `transcript.log` for retained checker stdout/stderr and diagnostics.
   A leaf record's `complete` field means finalization finished; it does not by
   itself mean outputs/checks passed. For a budgeted build, also follow the
   result's `controller` reference to its ordered attempt history and stop reason.
2. **Distinguish a defect from incomplete execution.** A check's exit `1`
   rejects behavior; `2` means missing tooling, invalid inputs or incomplete
   execution. For a missing Behave import, return to `APMX_SOURCE` and repair
   the environment for your installation route. With a prebuilt APMX, repeat
   the dependency repair for the existing
   [checks-only environment](../../../docs/install.md#example-tools-for-a-native-installation)
   without recloning or installing APMX into it. With source, rerun
   `uv sync --frozen --python 3.12 --extra factory` and reselect the documented
   PATH. For a managed source installation, use the
   [approved hash-verified repair process](../../../docs/install.md#managed-environment-installation-optional)
   instead of `uv sync`. Do not replace a failed check with a skip or treat exit `21` as success.
   For storage/cleanup failures, resolve the reported condition before retrying.
3. **Revise with your own harness, outside retained evidence.** Copy the failed
   run's `baseline/` and available `artifacts/` contents into a separate scratch
   directory for diagnosis. For an implementation failure, replay the two
   patch-aware checkers there against `changes.diff`; each invocation applies
   the patch to its own captured baseline. Use the diagnostics to improve the
   disposable factory's task instructions or request without changing the
   acceptance rules, protected checks or seed application. Keep the original
   `.apm/` records, artifacts and baselines untouched.
4. **Preview and rerun the factory.** Return to the caller containing
   `feature-factory`, run `apmx ./feature-factory --on copilot --plan`, then
   `apmx ./feature-factory --on copilot` after the preview succeeds. This is a
   new full attempt, not a resume from the failed node; it can incur new model
   costs. Keep both attempts and inspect the new chain's completion, all nine
   checks and all seven outputs before using anything.

For example, free delivery at `> 5000` rather than `>= 5000` must still be
rejected at exactly 5000 cents. Fix the proposed implementation, not the
threshold check. The controlled replay below demonstrates that rejection;
it is not evidence of an automatically repaired or failed model-generated run.

## Development observations (2026-10-06)

An unpublished local-source rehearsal at commit `40d7658` completed all five
stages, nine checks and seven deliveries through real Copilot. The build
natively loaded the pinned `python-testing-patterns` skill. The documentation
patch updated the real project page without changing the accepted code patch;
independent checker replays passed and the original consumer stayed unchanged.
The retained chain is `20261006T113040Z-be237cafe4f6`. This run predates the
build's repair budget and does not prove automatic repair.

A separate **controlled native repair experiment** at runtime commit `77c2dc2`
used a labelled build-task variant with a deliberately injected initial
`> 5000` defect. Both attempts used real Copilot, the same original project,
goal, pinned capability and unchanged acceptance checks. The first candidate
was rejected by both executable suites; the shared controller supplied its
rejected artifacts and diagnostics, and the second candidate passed all three
checks. Both records were retained under controller
`20261006T114927Z-cfbe7bcbf444`. Independent replays reproduced both outcomes;
the two attempts finished in approximately 99 seconds under a shared
two-attempt/300-second experimental budget. This demonstrates automatic native
repair under controlled fault injection, not an organically occurring failure
or an unchanged full-factory run.

Both observations used the operator-configured `gpt-5.6-sol` model without an
APMX model override. Their retained local records are not a published portable
Evidence Package, release, archive demonstration or OpenCode parity claim.
Historical recordings below remain separate and unchanged.

## Observed four-stage runs (historical)

These retained observations predate the documentation stage and stronger
document formats. They are not proof of a native five-stage run, OpenCode
parity or APM archive-resource preservation.

For the separate 2026-09-15 fresh-user source run, see the
[sanitized terminal replays and proof record](../../../docs/demo/README.md).
Download the self-contained HTML to open locally; the record distinguishes
actual execution time from edited playback and the post-run negative control.

On 2026-09-14, the factory command ran with real Copilot and a complete local
macOS ARM64 bundle, including its frozen artifact-tool server and bundled APM.
It preserved the configured model and completed all four contracts and six
checks, publishing five artifacts. Copilot invoked the Git exporter after
editing two source files and adding `tests/test_free_shipping.py`.

The retained patch passed ordinary `git apply --check` and `git apply` in a
fresh copy of the original application:

| Subtotal | Delivery | Total |
| --- | --- | --- |
| 4999 cents | 500 cents | 5499 cents |
| 5000 cents | 0 cents | 5000 cents |
| 5001 cents | 0 cents | 5001 cents |

All 14 required Gherkin cases passed. The original and generated regression
suites ran in separate processes. All 22 starting files remained unchanged,
and producer/check process cleanup was confirmed.

A controlled copy of that patch changed `>=` to `>` at the delivery threshold.
Both original patch-aware checks rejected the applied defect with exit `1`.
This negative control was deliberately introduced after the successful native
run; it was not a failed model-produced run or a modification of retained
evidence.

[The observation summary](observed-run.json) records the source snapshot,
binary and artifact hashes, chain ID, export observation and positive/negative
results. This local validation build is not a published release or a claim
that native inference ran on Windows/Linux. The factory remained
**UNPROVEN (exit 21)** despite passing checks.

## Limits and verification

This example accepts regular ASCII text edits to `src/pricing.py` and
`src/checkout.py`, plus a new `tests/test_free_shipping.py`. It rejects edits
to protected check resources, existing tests, symlinks, modes and other paths.
These are this example's patch requirements, not restrictions on all APMX
artifact types. The separate docs profile permits only regular text edits to
`docs/checkout.md` and requires every accepted non-doc byte to remain unchanged.
Arbitrary Python execution is not sandboxed.

The deterministic fixture suite uses actual Git exports and real check
processes. Its negative controls retain structurally valid patches but propose
the unchanged baseline or an incorrect `> 5000` threshold; supplied acceptance
rejects both even when generated tests make no useful assertion.

The observed run above supplements the deterministic suite; neither is
production certification or permission to merge/deploy. Historical v0.3.2
binaries, the pinned source and historical observation remain **UNPROVEN / 21**.
The released v0.4.2 example returns **COMPLETE / 0** only after all four
contracts, six checks and five artifacts have complete validated, finalized
evidence. This development example requires five stages, nine checks and seven
artifacts instead. Neither result establishes authenticated provenance or
enterprise governed execution.
Exit `21` still means noncomplete work or refused admission; inspect the
[versioned record](../../../docs/results.md). A rejected check
returns `20`, operational failure returns `22`, and preview returns `0`.

Reruns create new attempts without automatic retries. Earlier evidence remains
on disk. There is no automatic application, deployment or merge.
