# APMX

**APMX runs Agentic Software Factories from Markdown contracts. Your project is
the workspace. Declare each task's required inputs and handoffs, the artifacts
it must deliver, and the checks that must pass before another task can use them.**

**Start locally with your own harness. Share and version contracts through APM.
Share not just the skills for doing the work, but the goals and checks for
accepting it.**

**Released and development behavior are different.** The experimental
[v0.4.2 downloads](https://github.com/danielmeppiel/apmx/releases/tag/v0.4.2)
contain the earlier four-stage Copilot implementation. Follow the
[native installation guide](docs/install.md#install-a-prebuilt-archive) for
those immutable assets.

This development checkout adds implicit project capture, a five-stage factory,
bounded repair, portable evidence and a native
[OpenCode profile](docs/opencode.md), alongside Copilot. Follow the
[development example setup](examples/contracts/software-factory/README.md#set-up)
with this checkout selected, not the older pinned source. Authenticate in your
chosen harness; its installation and model access remain external prerequisites.

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
  plan-sections: python3 -I -B checks/documents.py planning plan.md
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
                        -> changes.diff + implementation.md
                        -> documentation.diff + documentation.md -> review.md
```

A **factory** is a directory of contracts and checks. APMX resolves their `needs`
into execution order. The consumer supplies its project and request; source
files do not need individual contract entries. No pipeline file or last-step
selection is required.

After [APMX installation](docs/install.md)
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

**Observed with real Copilot and OpenCode in development:** the same factory
definition completed all five stages and nine checks, retaining seven outputs
through each harness. Both produced actual code and documentation patches and
loaded the selected testing skill. A 5000-cent subtotal has free delivery;
4999 still costs 500 cents. These are source-route observations, not a claim
that the older downloads contain this implementation or that archive
distribution is ready.

## Deliver files, not a working directory

Any contract can produce one artifact or several. The implementation delivers:

```yaml
produces:
  - changes.diff
  - implementation.md
```

Copilot edits private source copies and invokes a bounded Git exporter; it
does not hand-write patch hunks. The original checkout is unchanged.
All declared artifacts must be retained and every required check must pass
before an authorized consumer receives any of that delivery.

The example's patch-aware checks reconstruct the changed application and test
it independently. **Gherkin is optional**; this example chooses it to express
behavior such as:

```gherkin
Scenario: Free delivery at 5000 cents
  Given a subtotal of 5000
  When I request pricing and checkout
  Then delivery is 0 cents and the total is 5000 cents
```

Use your own verification commands and tools. A section check can assess
document structure; it does not prove the reasoning is correct. Generated tests
do not replace the example's original acceptance checks.

## Follow the evidence

APMX retains artifacts, their identities and the original check results.
Missing outputs, failed or incomplete checks, and changed retained evidence
block dependent work. Follow the printed paths:

- `.apm/chains/<id>/record.json` connects the actual steps and handoffs.
- `.apm/chains/<id>/artifacts/` collects the completed factory's artifacts.
- `.apm/runs/<id>/record.json` records each contract's inputs and checks.

Every completed invocation also delivers a receipt: a CycloneDX ABOM,
in-toto/SLSA producer statements, Test Result statements, exact supporting
files and a generated summary.
[Inspect and independently validate the standard files](docs/evidence.md).
These are unsigned content bindings, not authenticated attestations or proof
that arbitrary prose is correct. Review captured source files before sharing.

For this development example, completion means five stages, nine required
checks and seven retained outputs. The build stage has an
[authored repair budget](docs/repair.md): at most three attempts sharing
600 seconds. Only eligible assessed rejections are retried, with unchanged
inputs and checks; operational failures stop. Other stages remain single-attempt.
Both current full rehearsals passed first time; a separate deliberately faulty
candidate demonstrated the two-attempt repair path.
[Recovery](examples/contracts/software-factory/README.md#recover-after-a-failed-or-incomplete-run)
is not automatic factory resume.

**Local execution is not a sandbox.** Agents and checks can use host files,
network and logins; model usage may cost money. Passing checks does not certify
isolation. Operational **COMPLETE (exit 0)**
only after all declared outputs are retained, every required check passed and
evidence is finalized.
Missing output, incomplete checks and policy/consent refusals remain nonzero.
In development, eligible evidence-delivery failure returns command exit 23
without rewriting a recorded COMPLETE execution.
The historical **v0.3.2 downloads and pinned demo still return UNPROVEN
(exit 21)** even when work completes; they are not rebuilt or reused by v0.4.2.
See [result and record migration](docs/results.md).

## Go further

APMX runs and checks; the selected harness does the agent work. Bundled APM prepares shared
packages and skills when needed, with no separate APM installation.

- [Factory setup, artifacts and manual recovery](examples/contracts/software-factory/README.md)
- [Write your first contract](examples/contracts/first-contract/README.md)
- [Reuse a packaged task and skill](examples/contracts/packaged-job/README.md)
- [Use the development OpenCode profile](docs/opencode.md)
- [Report a problem or contribute](https://github.com/danielmeppiel/apmx/issues) - include a small example and remove secrets from logs.

APMX is an independent project by Daniel Meppiel, licensed under Apache-2.0
for APMX-specific additions.
Retained Microsoft APM material remains MIT-licensed; its terms and attribution
are preserved in NOTICE. This is not an official Microsoft or GitHub release.
[Source origin](docs/source-origin.md) | [License](LICENSE) | [Notices](NOTICE)
