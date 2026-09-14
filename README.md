# APMX

APMX runs agentic software factories: **agents do the work; your checks decide
what moves forward.**

Go from a feature request to a plan, specification, code, tests and review
without writing agent-orchestration code.

## Start with what "done" means

An agent can write code, generate tests that share the same mistake, and still
say "done". A prompt alone does not give your workflow a reliable acceptance
check.

A **contract** is a Markdown file that gives the agent a task and tells APMX
what output to expect and how to check it. Here is the example's
[build contract](examples/contracts/software-factory/contracts/build.contract.md),
with its task instructions shortened:

```markdown
---
needs:
  - request.json
  - plan.json
  - spec.json
produces: shipping.py
verify:
  contract: python3 -I -B checks/verify.py build
---
Implement the shipping specification as shipping.py.
```

`needs` names the inputs; `produces` names the deliverable. `verify` is a check
**APMX runs independently**, not an instruction for Copilot to grade its own
work. You choose the checks, so acceptance does not depend on the agent's
conversation.

## Run a factory, not a list of agents

A **factory** is a directory of contracts, checks and starting inputs.
APMX connects each contract's inputs to the outputs it needs and works out the
order. No pipeline file, final-step selection or separate Python runner.

The [feature-factory example](examples/contracts/software-factory/README.md)
builds a Python shipping-cost function:

```text
Feature request -> Plan -> Specification -> Code -> Tests -> Review
```

Use the [matching APMX build](docs/install.md#run-the-current-source-checkout)
and [prepare the example folder](examples/contracts/software-factory/README.md#set-up).
From the directory containing `feature-factory`, run:

```sh
apmx ./feature-factory --on copilot
```

APMX shows the work and asks for confirmation before running it.
Add `--plan` to preview without model calls, package installation or checks.

The example's checks cover all 5000 valid shipping weights and invalid inputs.
Generated tests must match independent expected answers and catch four known
broken implementations. Code and tests agreeing with each other is not enough.

## Follow the evidence

After each agent finishes, APMX runs the original check definitions in separate
check workspaces. It saves the output, its hash and the check results. The
checked copy is what the next step receives; a newer same-name file cannot
silently replace it. Missing outputs or failed or incomplete checks block
dependent work.

Follow the printed paths inside your factory:

- `.apm/chains/<id>/record.json` connects the steps and explains blocked work.
- `.apm/chains/<id>/artifacts/` collects the completed factory's checked outputs.
- `.apm/runs/<run-id>/record.json` records each step's checks and input sources.

**Local execution is not a sandbox:** agents and checks can use your files,
network and logins, and model usage may cost money. Passing checks do not certify
isolation; local results remain **UNPROVEN (exit 21)**.
[Read the execution limits and results](examples/contracts/software-factory/README.md#failures-and-reruns).

## Go further

APMX runs and checks; Copilot does the agent work. Bundled APM prepares shared
packages and skills when needed, with no separate APM installation.

- [Factory walkthrough and output inspection](examples/contracts/software-factory/README.md)
- [Write your first contract](examples/contracts/first-contract/README.md)
- [Reuse a packaged task and skill](examples/contracts/packaged-job/README.md)
- [Automation and explicit permissions](examples/contracts/software-factory/README.md#automation)
- [Report a problem or contribute](https://github.com/danielmeppiel/apmx/issues) - include a small example and remove secrets from logs.

This private repository requires authorized access. APMX is an independent,
MIT-licensed extraction from Microsoft APM, not an official Microsoft release.
[Source origin](docs/source-origin.md) | [License](LICENSE) | [Notices](NOTICE)
