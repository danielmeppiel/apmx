# Run a feature factory

Give Copilot a feature request. Get a plan, specification, implementation,
tests and a review, with independent checks between steps.

This example builds a small Python shipping-cost calculator. The contracts
describe the work; APMX discovers their file dependencies and runs the factory.
You do not choose a final contract, write a pipeline or run a Python driver.

## Set up

Use the [current APMX build](../../../docs/install.md), Git, authenticated native
Copilot CLI and Python 3.12 or newer. Python runs this example's checkers;
APMX's native bundle includes its own runtime and APM backend.

From the source checkout on macOS/Linux, create a fresh example folder:

```sh
mkdir "$HOME/feature-factory" &&
cp -R examples/contracts/software-factory/. "$HOME/feature-factory/" &&
cd "$HOME"
```

If that name already exists, choose a new name in all three commands. Keep
previous results. Use an example folder outside any Git repository; do not
remove a real project's remotes or policy, or copy its data elsewhere, to
bypass admission.

## Run

From the folder containing `feature-factory`, with APMX on PATH:

```sh
apmx ./feature-factory --on copilot
```

APMX resolves the work and asks before running it locally. That confirmation
permits host access and lets independently passing local outputs move between
steps. It does not authorize rejected, missing or incompletely checked work.

**Only run factories you trust.** Copilot and checkers can use your files,
network and available logins. This is not a sandbox and model usage may cost
money. The configured Copilot model is preserved unless you set `--model`.

To inspect the order without running anything:

```sh
apmx ./feature-factory --on copilot --plan
```

This is APMX's dependency plan. It does not ask Copilot to write the example's
`plan.json`; that happens during the first step of execution.

### Automation

Pipes and CI never receive a confirmation prompt. Supply the permissions
explicitly:

```sh
apmx ./feature-factory --on copilot \
  --allow-host-access --allow-unproven-inputs
```

`--allow-host-access` alone does not permit unproven outputs to move downstream.
The additional flag accepts only local outputs whose required checks all
passed. Results remain UNPROVEN; no certification is implied.

### Windows

Use native `copilot.exe`, Git for Windows' `sh.exe`, Python 3.12 and a matching
APMX build. From the source checkout in PowerShell:

```powershell
py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw "Install the native Python 3.12 launcher first." }
$example = Join-Path $PWD "examples\contracts\software-factory"
$factory = Join-Path $env:TEMP ("feature-factory-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $factory -ErrorAction Stop | Out-Null
Copy-Item "$example\*" $factory -Recurse -ErrorAction Stop
Get-ChildItem (Join-Path $factory "contracts") -Filter "*.contract.md" | ForEach-Object {
  $text = [IO.File]::ReadAllText($_.FullName).Replace("python3 ", "py -3.12 ")
  [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
}
apmx $factory --on copilot --plan
if ($LASTEXITCODE -ne 0) { throw "Preview failed; inspect the reported cause." }
apmx $factory --on copilot
```

Only the disposable contract copies are adjusted for the Python launcher.
The source checkout stays unchanged.

## The contracts

```text
request.json -> plan.json -> spec.json -> shipping.py -> tests.json -> review.json
```

Later steps may also need earlier files directly. Those additional connections
are written in `needs`, not in a separate execution recipe.

| Contract | Output | What the independent checker assesses |
| --- | --- | --- |
| [Planning](contracts/planning.contract.md) | `plan.json` | Work breakdown, requirements and validation strategy |
| [Specification](contracts/specification.contract.md) | `spec.json` | Interface, rates, errors and boundary expectations |
| [Build](contracts/build.contract.md) | `shipping.py` | Restricted syntax, all 5000 valid weights and invalid inputs |
| [Test](contracts/test.contract.md) | `tests.json` | Expected results and detection of four known broken implementations |
| [Review](contracts/review.contract.md) | `review.json` | Report structure and references; the review itself remains advice |

The [request](request.json) defines `shipping_cost(weight_grams)`: return
integer cents for five weight tiers. Wrong types, including booleans, must
raise `TypeError`; integers outside 1..5000 must raise `ValueError`.

You supply [the checker](checks/verify.py). The agent supplies the implementation.
The checker has its own expected answers, so generated code and generated
tests agreeing with each other is not enough to pass.

For this small teaching task, generated Python is restricted to branches,
comparisons, integer returns and fixed error raises. No imports, loops or
arbitrary calls are accepted. That restriction is not a general Python sandbox.

## Inspect the results

Results stay inside the selected factory folder. APMX prints the aggregate
record path:

```text
feature-factory/.apm/chains/<id>/record.json
```

It connects each contract to its actual run, checked output and input sources.
Each step also keeps its ordinary record and output:

```text
feature-factory/.apm/runs/<run-id>/record.json
feature-factory/.apm/runs/<run-id>/artifacts/<output>
```

Use the printed paths. Do not guess the newest folder. Generated files do not
overwrite same-name files at the top of the factory folder.

A completed factory also collects the checked outputs, request and original
checks in its printed `Artifacts:` directory:

```text
feature-factory/.apm/chains/<id>/artifacts/
```

These are retained copies with recorded hashes and origins, not files copied
back into your project. Check that the factory record says `complete: true`;
directory existence alone does not establish completion.

Use that printed directory to test a quote:

```sh
artifacts_dir="PASTE_PRINTED_ARTIFACTS_DIRECTORY"
python3 -I -B "$artifacts_dir/checks/verify.py" quote \
  --directory "$artifacts_dir" --weight 1001
```

Expected output: `{"returns": 700}`. Using `--weight true` reports
`{"raises": "TypeError"}`. Inputs are JSON values, not Python expressions.

Run the generated test cases through the trusted checker:

```sh
python3 -I -B "$artifacts_dir/checks/verify.py" test \
  --directory "$artifacts_dir"
```

Read `review.json` in that directory too. A completed factory can legitimately contain
review findings; completion is not a claim that the code has no problems.

## Observed run

The directory command above was exercised with real GitHub Copilot CLI
1.0.84-5 on macOS ARM64, using the native APMX build from
`3dd76889497b29bf1418c280868859878b628ee5`. No model override was supplied.
The interactive prompt appeared before run allocation; an empty answer refused
without creating state, and an explicit yes started the factory.

Run `20260914T132012Z-a5f00ea99b7a` completed all five contracts with every
required check passing. Its nine input handoffs matched the exact retained
predecessor files and record hashes. The collected output included 23 generated
test cases and an advisory review reporting no findings. Original factory
files were unchanged, and producer/checker cleanup was confirmed.

The retained checker was then run against the collected outputs: all five
checks passed again; weight `1001` returned `700` cents and `true` raised
`TypeError`. Completion remained **UNPROVEN/21**, not production certification.
Case counts and review findings can differ on another model run.

For a controlled negative check, a separate copy changed only
`weight_grams <= 1000` to `weight_grams < 1000`. The original checker rejected it:

```text
Failed condition: Acceptance failed for 1000.
```

That defect was deliberately introduced to exercise the checker; it was not
generated by Copilot. The real run's captured output and evidence were not
modified.

## Failures and reruns

**A completed local run still exits 21 (UNPROVEN).** Passing checks are not
production certification. Exit 21 can also mean incomplete work, so read the
record's completion, checks and blocked-step reasons, not just its exit code.

A rejected check returns 20; operational failure or cancellation returns 22.
A successful preview returns 0 without executing the factory.

Missing inputs, duplicate output owners and cycles refuse before model work.
Rejected or undecided checks block dependent steps. A stale file from an
earlier run cannot stand in for the checked output.

Rerunning creates new attempts; previous evidence stays on disk. Native
execution has no complete observed read set, so it does not reuse prior
results as a cache. There are no automatic retries, parallel jobs, deployments
or merges.

Each step retains the existing 1200-second attempt watchdog, 180-second check
timeout and bounded process cleanup. These are supervision limits, not proof
that an unrestricted native process cannot escape.

## Packages and skills

These local contracts have no imports, so no APM installation is needed.
The [packaged handoff](../packaged-job/README.md) shows bundled APM preparing
a selected package and skill. The factory and single-contract paths reuse the
same execution and checking machinery.
