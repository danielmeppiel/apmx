# Run a checkout feature factory

Give Copilot a checkout feature request. Get a plan, specification, code patch,
implementation report and advisory review, with independent checks between
contracts. APMX follows artifact dependencies; there is no separate pipeline
script or phase-name convention.

The seed application charges 500 cents for delivery. The requested change makes
delivery free from a 5000-cent subtotal while preserving the fee below it.
Both pricing and checkout totals must agree.

## Set up

Complete the [pinned source installation](../../../docs/install.md#run-the-current-source-checkout)
first, including backend provisioning and the `factory` extra. Stay in that
terminal: `APMX_SOURCE` identifies the checkout and PATH selects its APMX and
Python 3.12. **Behave 1.3.3 is optional for APMX**, but required for this
example's Gherkin checks. The legacy v0.2.0 binary cannot run this factory.

On macOS or Linux, check the selected tools before copying anything:

```sh
command -v apmx
python3 -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
git --version
copilot --version
```

APMX and Python must come from the source checkout's `.venv/bin/`, and Behave
must report `1.3.3`. Copilot must be installed and authenticated through its own
CLI; a version response does not establish login. Stop on any missing tool.

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
  cd "$demo" &&
  printf 'Demo directory: %s\n' "$PWD"
fi
```

Keep the printed directory: outputs and records will live below it, not in
your source installation. Temporary folders can be cleaned by your operating
system; preserve the complete demo directory somewhere durable when finished.
If your chosen location is inside Git, choose another location; never remove
a real project's remotes or policy to bypass admission.

### Windows

Complete the [Windows source installation](../../../docs/install.md#windows-source-installation)
first. In that same PowerShell session, make a new caller and adjust only the
disposable contract copies to use `python` from the selected `.venv\Scripts`
environment. A separate `py -3.12` launcher could select an environment without
Behave, so it is not used here.

```powershell
$demo = Join-Path $env:TEMP ("apmx-demo-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $demo -ErrorAction Stop | Out-Null
git -C $demo rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -eq 0) { throw "Choose a demo location outside any Git repository." }
$factory = Join-Path $demo "feature-factory"
New-Item -ItemType Directory -Path $factory -ErrorAction Stop | Out-Null
Copy-Item "$(Join-Path $env:APMX_SOURCE 'examples\contracts\software-factory')\*" $factory -Recurse -ErrorAction Stop
Get-ChildItem (Join-Path $factory "contracts") -Filter "*.contract.md" | ForEach-Object {
  $text = [IO.File]::ReadAllText($_.FullName).Replace("python3 -I -B checks/", "python -I -B checks/")
  [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
}
Set-Location $demo
Write-Output "Demo directory: $demo"
```

Keep the selected environment on PATH. The original checkout and check resources
are unchanged. The remaining APMX commands work in PowerShell too; use `python`
instead of `python3` for manual checker replay.

## What the contracts deliver

```text
request.md -> plan.md -> specification.md -> changes.diff + implementation.md -> review.md
```

Each `needs` list names the artifacts that contract consumes, including initial
source files where needed. Several inputs from the same producer cause one
producer execution, not several.

| Contract | Published artifact(s) | Independent verification |
| --- | --- | --- |
| [Planning](contracts/planning.contract.md) | `plan.md` | Nonempty planning sections |
| [Specification](contracts/specification.contract.md) | `specification.md` | Nonempty behavior, interface and acceptance sections |
| [Implementation](contracts/build.contract.md) | `changes.diff`, `implementation.md` | Apply patch and run supplied Gherkin; separately apply patch and run regressions; check report sections |
| [Advisory review](contracts/review.contract.md) | `review.md` | Nonempty advisory-review sections |

Markdown checks assess structure, not reasoning or application correctness.
The [request](request.md) and [supplied acceptance](checks/features/free-shipping.feature)
remain authoritative. Producers are not asked to claim that they ran checks.

The implementation contract publishes files, not source-directory write scopes:

```yaml
produces:
  - changes.diff
  - implementation.md
verify:
  acceptance: python3 -I -B checks/acceptance.py changes.diff
  regression: python3 -I -B checks/regression.py changes.diff
  report: python3 -I -B checks/documents.py implementation implementation.md
```

Copilot edits its private source copies and uses the runtime's bounded Git
export tool to create the patch. It also writes the Markdown report. APMX
publishes those two declared artifacts, not the whole working directory.
The caller's source files are never overwritten.

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

Expect four contracts in dependency order: planning, specification, build and
review; five declared output files; and six checks. Preview exits `0` and does
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
record and confirm `complete: true`, all four `nodes` are `completed`, no
`result.stop_reason`, and all six checks have `normalized: 0` in the node
results/per-run records. Check names repeat across contracts: planning and
specification each have `document`, build has `acceptance`, `regression` and
`report`, and review has `document`.

The completed artifact view must contain all five deliveries:
`plan.md`, `specification.md`, `changes.diff`, `implementation.md` and `review.md`.
Read the documents and review the patch yourself. The original source checkout
is unchanged; APMX does not automatically apply the patch to a project.

Replay either patch-aware checker from that artifact view:

```sh
printf 'Paste the printed Artifacts directory: '
IFS= read -r artifacts
if cd "$artifacts"; then
  python3 -I -B checks/acceptance.py changes.diff
  printf 'Acceptance exit: %s\n' "$?"
  python3 -I -B checks/regression.py changes.diff
  printf 'Regression exit: %s\n' "$?"
fi
```

**Each invocation starts from the captured baseline, applies the exact patch,
and tests that candidate.** A separate `git apply --check` followed by an
unrelated test invocation would not test the same workspace.

On PowerShell, use `Set-Location (Read-Host "Paste the printed Artifacts directory")`
and the same checker commands with `python`. Inspect each check's JSON and exit
status (`$?` immediately after a check in a POSIX shell, `$LASTEXITCODE` in
PowerShell); do not let the second invocation hide the first one's failure.

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

- `0`: every required example and required test executed and passed.
- `1`: application behavior or a test assertion failed.
- `2`: tooling, inputs, patch application or execution was invalid/incomplete.

Empty, filtered, skipped, undefined or pending Gherkin does not pass. External
Behave configuration and environment selection are ignored. Missing optional
Behave returns `2` with setup guidance, not a passing skipped check.

## Recover after a failed or incomplete run

APMX stops dependent work; it does **not** automatically repair, retry or resume
the factory. A stopped factory may have per-run artifacts but no completed
aggregate `artifacts/` view.

1. **Find the first stopped contract.** Open the printed chain `record.json`.
   Read `result.stop_reason`, the stopped node's `reason` and `result`, and its
   referenced run directory. In that run's `record.json`, inspect required
   `checks`, their `normalized` values and raw `process` observations. Read the
   adjacent `transcript.log` for retained checker stdout/stderr and diagnostics.
   A leaf record's `complete` field means finalization finished; it does not by
   itself mean outputs/checks passed.
2. **Distinguish a defect from incomplete execution.** A check's exit `1`
   rejects behavior; `2` means missing tooling, invalid inputs or incomplete
   execution. For a missing Behave import, return to `APMX_SOURCE`, rerun
   `uv sync --frozen --python 3.12 --extra factory`, and reselect the documented
   PATH. Do not replace a failed check with a skip or treat exit `21` as success.
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
   costs. Keep both attempts and inspect the new chain's completion, all six
   checks and all five outputs before using anything.

For example, free delivery at `> 5000` rather than `>= 5000` must still be
rejected at exactly 5000 cents. Fix the proposed implementation, not the
threshold check. The controlled replay below demonstrates that rejection;
it is not evidence of an automatically repaired or failed model-generated run.

## Observed run

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
artifact types. Arbitrary Python execution is not sandboxed.

The deterministic fixture suite uses actual Git exports and real check
processes. Its negative controls retain structurally valid patches but propose
the unchanged baseline or an incorrect `> 5000` threshold; supplied acceptance
rejects both even when generated tests make no useful assertion.

The observed run above supplements the deterministic suite; neither is
production certification or permission to merge/deploy. Passing checks remain
**UNPROVEN**. Completed local execution exits `21`; that code
can also accompany incomplete work, so inspect the record. A rejected check
returns `20`, operational failure returns `22`, and preview returns `0`.

Reruns create new attempts without automatic retries. Earlier evidence remains
on disk. There is no automatic application, deployment or merge.
