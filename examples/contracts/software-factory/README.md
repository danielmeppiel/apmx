# Run a checkout feature factory

```sh
apmx ./feature-factory --on copilot
```

Give Copilot a checkout feature request. Get a plan, specification, code patch,
implementation report and advisory review, with independent checks between
contracts. APMX follows artifact dependencies; there is no separate pipeline
script or phase-name convention.

The seed application charges 500 cents for delivery. The requested change makes
delivery free from a 5000-cent subtotal while preserving the fee below it.
Both pricing and checkout totals must agree.

## Set up

Use the [current APMX build](../../../docs/install.md), Git, authenticated native
Copilot CLI, and Python 3.12 or newer. **Behave is optional for APMX.** This
example chooses Gherkin acceptance and therefore needs **Behave 1.3.3** in the
Python environment used by its checks. Other contracts can use other tools.

For source-checkout development, the optional extra is:

```sh
uv sync --frozen --extra dev --extra factory
. .venv/bin/activate
```

For a native APMX binary, use a separate Python environment without installing
APMX into it, so it does not shadow your installed binary:

```sh
python3 -m venv .checkout-checks
. .checkout-checks/bin/activate
python3 -m pip install 'behave==1.3.3'
```

Keep that environment active while running APMX and replaying checks. The
native bundle supplies its own APMX runtime, not this example's optional tools.
Do not install packages globally.

From the source checkout, copy the example to a new directory:

```sh
mkdir "$HOME/feature-factory" &&
cp -R examples/contracts/software-factory/. "$HOME/feature-factory/" &&
cd "$HOME"
```

If that directory exists, choose a new name; preserve earlier runs. Use a demo
directory outside another Git repository. Do not remove a real project's
remotes or policy to bypass admission.

### Windows

Use native Copilot, Git for Windows, Python 3.12 or newer and a matching APMX
build. Create and activate the optional check environment in PowerShell:

```powershell
py -3.12 -m venv .checkout-checks
& .\.checkout-checks\Scripts\Activate.ps1
python -m pip install 'behave==1.3.3'
```

Copy the example to a new directory under your home directory. In the copied
contracts, replace `python3 ` with `python ` so checks use the active environment,
not a different Python launcher. Then preview and run that directory with APMX.

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

Then run the first command on this page. APMX requests host-access and local
handoff consent before execution. The configured model is preserved unless
you explicitly select another model. Only run trusted contracts: production
and checks execute on your host, not in a sandbox, and model usage can cost money.

### Automation

Automation must supply consent explicitly:

```sh
apmx ./feature-factory --on copilot \
  --allow-host-access --allow-unproven-inputs
```

The second permission admits only complete local deliveries whose required
checks passed. It does not permit failed or incomplete outputs.

### Artifacts and checks

Use the printed aggregate record and `Artifacts:` directory:

```text
feature-factory/.apm/chains/<id>/record.json
feature-factory/.apm/chains/<id>/artifacts/
```

The artifact view contains the exact admitted outputs and original required
inputs/check resources. A directory existing is not proof of completion; read
the record's `complete` field and required check results.

Replay either patch-aware checker from that artifact view:

```sh
cd "PASTE_PRINTED_ARTIFACTS_DIRECTORY"
python3 -I -B checks/acceptance.py changes.diff
python3 -I -B checks/regression.py changes.diff
```

**Each invocation starts from the captured baseline, applies the exact patch,
and tests that candidate.** A separate `git apply --check` followed by an
unrelated test invocation would not test the same workspace.

The regression command runs the supplied cases and original tests in one fresh
Python process, then generated tests in another. Generated-test imports and mocks
cannot alter the original suite's observed results. Both suites must complete;
passing generated tests never cancels an original regression failure.

Each checker prints bounded JSON with baseline, patch, candidate and check
resource hashes, required example identities and observed statuses. APMX
retains check stdout with its ordinary evidence; the example does not invent
runtime records. The checker removes its own private reconstruction directory.

Checker exit codes:

- `0`: every required example and required test executed and passed.
- `1`: application behavior or a test assertion failed.
- `2`: tooling, inputs, patch application or execution was invalid/incomplete.

Empty, filtered, skipped, undefined or pending Gherkin does not pass. External
Behave configuration and environment selection are ignored. Missing optional
Behave returns `2` with setup guidance, not a passing skipped check.

## Observed run

On 2026-09-14, the command above ran with real Copilot and a complete local
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
