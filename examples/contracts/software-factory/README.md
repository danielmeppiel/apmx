# Five contracts, one small software change

This example builds a tiny Python shipping-quote library through separate
**planning, specification, build, test, and review contracts**.

The contrast is not "five agents said they finished." It is: **here are the
exact results, what separate checks observed, and what remains unproven**.

A standard-library Python script chains ordinary one-contract apmx invocations.
There is no workflow engine in apmx, new contract syntax, skill dependency,
automatic retry, deployment, or approval to merge.

These are direct local contracts without imports, so no APM installation step
is needed. The [packaged handoff](../packaged-job/README.md) separately shows
bundled APM preparing a package and its selected skill.

## Run

Use Python **3.12 or later**, Git, authenticated native Copilot CLI, and a
trusted **current-checkout apmx build**. Windows also needs native `copilot.exe`
and Git for Windows' `sh.exe`; npm `.cmd` shims are not supported. See the
[installation/build guide](../../../docs/install.md).

Published v0.2.0 and the current checkout are different builds despite the
shared version string. These example instructions target the current checkout;
they do not promise unpublished features in an older release archive.

From this repository's checkout on macOS/Linux, with APMX on PATH:

```sh
python3 -B examples/contracts/software-factory/run.py \
  --apmx "$(command -v apmx)" \
  --workspace "$HOME/apmx-factory-demo/run-001" \
  --allow-host-access
```

On PowerShell, using Python 3.12 and the native APMX command on PATH:

```powershell
py -3.12 -B examples/contracts/software-factory/run.py `
  --apmx (Get-Command apmx).Source `
  --workspace "$HOME/apmx-factory-demo/run-001" `
  --allow-host-access
```

Choose a **new path outside every existing Git repository**. The script creates
it and refuses to reuse it. It never strips a project's remotes/policy, edits
your source checkout, or overwrites an existing demo. Do not copy a governed
project elsewhere to bypass its policy.

The native model keeps its configured default. Add `--model MODEL` only if you
want an explicit override. One complete run uses five model-bearing invocations,
plus five non-inference native MCP inventory calls. There is no price estimate
or hard spending cap.

**Trust the contracts and checkers before granting host access.** Native
execution and checks use your host identity. Tool restrictions and the small
Python syntax check below are not a sandbox or malicious-model containment.

Use `python3 examples/contracts/software-factory/run.py --help` for options.
The host binds checker commands to that Python interpreter, so Windows does
not require a separate `python3` alias inside the check shell.

## What each phase does

| Contract | New artifact | Separate checker |
|---|---|---|
| [Planning](contracts/planning.contract.md) | `plan.json`: ordered work, requirement coverage, risks, validation strategy | Checks structure, fixed phase order, and coverage |
| [Specification](contracts/specification.contract.md) | `spec.json`: callable interface, rates, errors, boundary examples | Compares semantics and case expectations to the fixed task |
| [Build](contracts/build.contract.md) | `shipping.py`: actual callable Python | Checks the small AST subset and all 5000 valid integer weights, plus invalid inputs |
| [Test](contracts/test.contract.md) | `tests.json`: executable JSON cases with reasons | Runs cases on captured code, checks expectations independently, requires four known mutants to be exposed |
| [Review](contracts/review.contract.md) | `review.json`: findings, limitations, follow-ups | Checks report shape and references; the review judgment remains advisory |

The task in [request.json](request.json) is deliberately small:
`shipping_cost(weight_grams)` returns integer cents for five weight tiers.
Wrong types (including booleans) raise `TypeError`; integers outside 1..5000
raise `ValueError`.

The generated function uses a task-specific branch-only subset: comparisons,
integer returns, and explicit error raises. No imports, loops, arithmetic,
attributes, arbitrary calls, or external effects are accepted. This keeps the
teaching task bounded; it is not a general Python executor or security boundary.

Generated tests are **not** the authority for correctness. The trusted
[verifier](checks/verify.py) owns one independent task oracle. It checks every
generated expectation and rejects incorrect code even when generated tests
agree with that code. Review format checks do not establish the quality of
the reviewer's reasoning.

## Inspect the exact results

Progress is written to stderr; the final JSON summary is written to stdout and
persisted in `<workspace>/factory-run.json`.

```text
<workspace>/
  factory-run.json
  stages/
    01-planning/
      request.json
      job.contract.md
      checks/
      .apm/runs/<run-id>/record.json
      .apm/runs/<run-id>/artifacts/plan.json
    02-specification/
      plan.json                  # exact admitted predecessor bytes
      ...
    03-build/...
    04-test/...
    05-review/...
  result/                        # only after all five stages were admitted
    request.json
    plan.json
    spec.json
    shipping.py
    tests.json
    evidence.json
    review.json
```

apmx does **not** write the declared output at the caller root. It captures it
under that run's `artifacts/`. Only the host script copies validated retained
bytes into the next caller. Each phase has a fresh native context.

The ledger records exact run paths, contract/input/output hashes, and actual
check observations. `review` contains the full advisory findings and follow-ups;
read it even if `complete` is true.

```sh
python3 -m json.tool "$HOME/apmx-factory-demo/run-001/factory-run.json"
```

Current apmx has **no `--json` flag or machine record-locator output**. This
example runs once in each exclusively created caller and requires exactly one
record in that caller's `.apm/runs/`. It never sorts by modification time, guesses
"latest", or interprets a model's printed paths as commands or file authority.

Before a handoff, the host checks the record, expected checks, original and
captured inputs/resources, retained artifact size/digest, and completion/cleanup
observations. Missing or inconsistent evidence stops the chain. These are
same-user local observations, not signed or tamper-proof evidence.

## Execute through the trusted verifier

After inspecting the results, run an actual quote through the checker, which
validates the restricted source and its fixed acceptance suite before calling it:

```sh
python3 -I -B examples/contracts/software-factory/checks/verify.py quote \
  --directory "$HOME/apmx-factory-demo/run-001/result" --weight 1001
```

Expected output: `{"returns": 700}`. `--weight true` reports
`{"raises": "TypeError"}`. Inputs are JSON values, not Python or shell commands.

Re-execute the generated JSON test cases against the captured implementation:

```sh
python3 -I -B examples/contracts/software-factory/checks/verify.py test \
  --directory "$HOME/apmx-factory-demo/run-001/result"
```

Checker exit 0 means its conditions passed. It does not change the factory's
UNPROVEN assessment, certify isolation, or authorize production use.

## Outcomes and stopping

| Host exit | Meaning |
|---|---|
| 2 | Invalid runner arguments; inspect `--help` and correct the invocation |
| 20 | An independent check rejected a phase; later phases did not run |
| 21 | UNPROVEN: may be a completed chain **or** missing/incomplete evidence |
| 22 | Operational failure, cancellation, timeout, or inconsistent evidence |
| 0 | Help only; not an executing chain's certification |

**A completed native run still exits 21.** Inspect `complete`, the five admitted
stage entries, and the advisory review; do not treat every 21 as completion or
hide it behind `|| true`.

No stage retries automatically. Keep failed evidence, fix the trusted
contract/checker or environment, then deliberately choose a new workspace.
Never promote files from an incomplete chain manually merely because they exist.

apmx bounds each admitted attempt to 1200 seconds and each check to 180 seconds.
The host allows 1260 seconds per invocation. On timeout/cancellation it gives
apmx 15 seconds for cleanup before escalating; it stops regardless of any
subsequent success-looking record. Killing a wrapper does not establish
descendant containment. Inspect processes before another run if cleanup is
uncertain.

## Validation scope

Deterministic tests exercise the full source CLI, captured artifacts, real
independent check processes, and the five host handoffs with only producer
execution substituted. Other tests cover record tampering, incomplete statuses,
quoting, syntax limits, and the independent oracle.

Those fixtures are **not live model acceptance**.

A separate native demonstration on 2026-09-14 used macOS ARM64, host Python
3.14.3, and the local APMX runtime from source commit
`3f58cc99c54152e3eac8f2b3cade72c7385726a1`. Copilot's configured default reported
`gpt-5.6-sol`; no model override or automatic retry was used. All five phases
completed with passing independent checks, nine predecessor-artifact handoffs
matched byte-for-byte, and the result remained **UNPROVEN/21**.

The generated suite contained 23 cases. The trusted verifier returned
`{"returns": 700}` for 1001 grams and `{"raises": "TypeError"}` for `true`.
The review returned an advisory coverage follow-up rather than `no_findings`;
it was preserved, not edited away to manufacture a clean result. This is one
observed run, not a model-reliability benchmark. Native Windows/Linux execution
and production readiness are not established by it.
