# APMX

**APMX runs Agentic Software Factories from Markdown contracts. Declare the files
each task in your factory needs, the artifacts it must deliver, and the checks
that must pass before another factory task can use them.**

**Start locally with your own harness. Share and version contracts through APM.
Share not just the skills for doing the work, but the goals and checks for
accepting it.**

**Experimental source preview.** Start with the
[pinned source installation](docs/install.md#run-the-current-source-checkout),
then [copy, preview and run the factory](examples/contracts/software-factory/README.md#set-up).
This preview is **source-only**:
[native downloads are temporarily withheld](docs/install.md#native-downloads-temporarily-withheld)
pending third-party notice remediation. Current harness support is **native
GitHub Copilot CLI only**; authenticate with Copilot before execution.

## Start with a contract

A **contract** is a Markdown task with three declarations: what it needs,
what files it must deliver, and how to check them. Those files are its
**artifacts**: a plan, patch, report or binary.

Here is the example's [planning contract](examples/contracts/software-factory/contracts/planning.contract.md),
with its instructions shortened:

```markdown
---
needs: request.md
produces: plan.md
verify:
  document: python3 -I -B checks/documents.py planning plan.md
---
Plan the checkout change in request.md. Write plan.md with
Goal, Changes, Validation and Risks sections.
```

The next contract declares `needs: plan.md`. APMX runs the planner, checks its
output, and passes the exact captured document forward. You do not write the
agent-orchestration code.

## Run a feature factory

The [checkout example](examples/contracts/software-factory/README.md) adds free
delivery from a 5000-cent subtotal, changing pricing, checkout and tests.
Its contracts deliver:

```text
request.md -> plan.md -> specification.md
                        -> changes.diff + implementation.md -> review.md
```

A **factory** is a directory of contracts, checks and starting inputs. APMX
resolves their `needs` into execution order. No pipeline file or last-step
selection.

After [source installation](docs/install.md#run-the-current-source-checkout)
and [example setup](examples/contracts/software-factory/README.md#set-up),
preview with `apmx ./feature-factory --on copilot --plan` from the disposable
directory containing `feature-factory`. Continue only if the preview succeeds,
then run:

```sh
apmx ./feature-factory --on copilot
```

Preview makes no model calls, installs nothing and runs no checks; it does not
verify Copilot login or checker readiness. Execution shows the work and asks
for confirmation.

**Observed with real Copilot and a local native macOS build:** all four
contracts and six checks completed. The patch changed two source files and
added regression tests. A 5000-cent subtotal now has free delivery; 4999 still
costs 500 cents. In a separate checker replay,
a [deliberately broken patch was rejected at the threshold](examples/contracts/software-factory/README.md#observed-run).

## Deliver files, not a working directory

Any contract can produce one artifact or several. The implementation delivers:

```yaml
produces:
  - changes.diff
  - implementation.md
```

Copilot edits private source copies and invokes a bounded Git exporter; it
does not hand-write patch hunks. The original checkout is unchanged.
All declared artifacts and required checks must be complete before a consumer
receives any of that delivery.

The example's patch-aware checks reconstruct the changed application and test
it independently. **Gherkin is optional**; this example chooses it to express
behavior such as:

```gherkin
Scenario: Free delivery at 5000 cents
  Given a subtotal of 5000
  When I request pricing and checkout
  Then delivery is 0 cents and the total is 5000 cents
```

Use your own verification commands and tools. A document check can assess
structure; it does not prove the reasoning is correct. Generated tests do not
replace the example's original acceptance checks.

## Follow the evidence

APMX retains artifacts, their identities and the original check results.
Missing outputs, failed or incomplete checks, and changed retained evidence
block dependent work. Follow the printed paths:

- `.apm/chains/<id>/record.json` connects the actual steps and handoffs.
- `.apm/chains/<id>/artifacts/` collects the completed factory's artifacts.
- `.apm/runs/<id>/record.json` records each contract's inputs and checks.

For this example, completion means all four contracts, all six required checks
and all five artifacts are present. If a check fails, dependent work stops.
[Inspect diagnostics, revise with your own harness and rerun](examples/contracts/software-factory/README.md#recover-after-a-failed-or-incomplete-run);
APMX does not automatically repair or resume the factory.

**Local execution is not a sandbox.** Agents and checks can use host files,
network and logins; model usage may cost money. Passing checks does not certify
isolation. Local results remain **UNPROVEN (exit 21)**; inspect the record to
distinguish a completed run from incomplete work.

## Go further

APMX runs and checks; Copilot does the agent work. Bundled APM prepares shared
packages and skills when needed, with no separate APM installation.

- [Factory setup, artifacts and manual recovery](examples/contracts/software-factory/README.md)
- [Write your first contract](examples/contracts/first-contract/README.md)
- [Reuse a packaged task and skill](examples/contracts/packaged-job/README.md)
- [Report a problem or contribute](https://github.com/danielmeppiel/apmx/issues) - include a small example and remove secrets from logs.

APMX is an independent project by Daniel Meppiel, licensed under Apache-2.0
for APMX-specific additions.
Retained Microsoft APM material remains MIT-licensed; its terms and attribution
are preserved in NOTICE. This is not an official Microsoft or GitHub release.
[Source origin](docs/source-origin.md) | [License](LICENSE) | [Notices](NOTICE)
