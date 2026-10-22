---
title: FAQ
description: "Short answers: where APMX fits, and its honest limits."
---

## How does APMX relate to GitHub Agentic Workflows?

They are complementary. [GitHub Agentic Workflows](https://github.github.com/gh-aw/)
(gh-aw) secures the agent's run inside GitHub Actions. APMX makes the factory
portable and its output verifiable at merge.

- **gh-aw** compiles Markdown workflows to GitHub Actions, triggered by
  schedules or repository events. It guards the run: a
  sandboxed, read-only agent behind a network firewall, writing only through
  declared safe outputs, on your choice of engine.
- **APMX** runs a factory: a set of Agent Contracts (assignment, role,
  dependencies, budget, checks, deliverables), versioned as an APM package.
  The same factory version runs on your machine or in CI, on your choice of
  harness and model. Trust is decided at merge, in the receiving repository:
  one unsigned in-toto receipt bound to the delivered files, covered by
  GitHub attestations from a trusted signer, and verified against that
  repository's policy. The signed verification summary binds every receipt
  line and delivered file by digest.

| | gh-aw | APMX |
| --- | --- | --- |
| Unit | A Markdown workflow | A factory of Agent Contracts |
| Runs in | GitHub Actions (you author and compile locally) | Your machine or CI |
| Guards | The run | The merge |

Honest notes: gh-aw also offers a choice of engine; APMX's portability means
running the same factory on your machine or in CI. gh-aw sandboxes the run;
APMX's runs are not sandboxed. gh-aw's `.lock.yml` is the compiled workflow,
checked for staleness against its source; an APMX receipt is evidence about
the delivered files, verified by another repository at merge.

**Together:** a gh-aw workflow could run an APMX factory: run-time safety
from gh-aw, merge-time evidence from APMX. That is a possible combination,
not a shipped integration.

## What does APMX promise?

APMX organizes its claims as four promises: Traceability, Portability,
Security, Trust. Each holds under stated conditions and has stated limits;
see the [trust model](/apmx/docs/trust-model/#four-promises).

## What is an agentic software factory?

An agentic software factory is a set of agents that turns intent into an
outcome, usually software. In APMX it is a directory or APM package of Agent
Contracts and their checks. See [factories](/apmx/docs/factories/).

## Which harnesses and models does APMX support?

Your choice of harness and model, within what exists today: GitHub Copilot
CLI (the default) and OpenCode 1.2.24, with the model you pass to the harness
(`--model`) or its default.
Each stage records the harness and models it reported; no policy enforces
them. See [choose your harness and model](/apmx/docs/any-harness/).

## Is a run sandboxed?

No. A run is not sandboxed: the agent and your checks can use your host
files, network and logins. Run only contracts you trust. In CI, `run.yml`
separates the agent, the checks and the signer into jobs on fresh runners.
See the [trust model](/apmx/docs/trust-model/).

## Does the budget cap cost?

No. A budget is what the job may spend; today attempts and time are
enforced, cost is not capped. See
[Agent Contracts](/apmx/docs/agent-contracts/#budget-what-the-job-may-spend).

## Why is my local receipt unsigned?

A receipt you make on your machine is unsigned: it binds content, not
identity. The receipt file is never signed, in CI either. Only the trusted
signer in `run.yml` (its `attest` job) signs, in CI: it creates GitHub
attestations, including a verification summary that binds every receipt line
and delivered file by digest. See
[the receipt](/apmx/docs/receipt/#signed-or-unsigned).

## Does VALID mean the code is correct?

No. A receipt is evidence of how a change was made, not a guarantee that the
code is correct. Checks are the bar: code that passes weak checks still
passes. See [audit verdicts](/apmx/docs/verdicts/#what-valid-does-not-mean).

## Why does apmx refuse to run in my repository?

APMX refuses to run inside a Git repository that has a remote; run from a
copy outside it. It also refuses when `apm.yml` has a `policy` key or when
`APM_POLICY_DISABLE=1` or `APM_NO_SCRIPTS` is set. `run.yml` stages a copy
for you. See the [`apmx` reference](/apmx/docs/reference/apmx/#where-it-refuses-to-run).

## Can it gate pull requests from outside contributors?

No. The gate passes only factory deliveries; human and third-party pull
requests fail it by design. Merge those through your normal review, for
example a ruleset bypass for maintainers. See
[auto-merge factory PRs](/apmx/docs/accept-prs/#merge-everything-else-the-normal-way).

## Does APMX comment on pull requests?

No. In a pull request it is one required check: green or red, with the audit
in the job log.

## Why do I see Ingredients and Standards rows?

Those are the v0.7.1 row names. The next release renames Ingredients to
Dependencies and folds Standards into Integrity. See
[changes since v0.7.1](/apmx/docs/reference/audit/#changes-since-v071).
