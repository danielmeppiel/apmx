---
title: FAQ
description: Short answers about where APMX fits next to other tools.
---

## How does APMX relate to GitHub Agentic Workflows?

They are complementary. [GitHub Agentic Workflows](https://github.github.com/gh-aw/)
(gh-aw) secures the agent's run inside GitHub Actions. APMX makes the factory
portable and its output verifiable anywhere.

- **gh-aw** compiles Markdown workflows to GitHub Actions, triggered by
  schedules or repository events. It guards the run: a
  sandboxed, read-only agent behind a network firewall, writing only through
  declared safe outputs, on your choice of engine.
- **APMX** runs a factory: a set of Agent Contracts (intent, deliverables,
  checks, budget), versioned as an APM package. The same factory version runs
  on a laptop, in CI or on any machine, on your choice of harness and model.
  Trust is decided at merge, in the receiving repository: one in-toto receipt
  bound to the delivered files, signed by a trusted signer and verified
  against that repository's policy.

| | gh-aw | APMX |
| --- | --- | --- |
| Unit | A Markdown workflow | A factory of Agent Contracts |
| Runs in | GitHub Actions (you author and compile locally) | A laptop, CI or any machine |
| Guards | The run | The merge |

Honest notes: gh-aw also offers a choice of engine; APMX's portability means
running the same factory anywhere. gh-aw sandboxes the run; APMX's local runs
are not sandboxed. gh-aw's `.lock.yml` is the compiled workflow, checked for
staleness against its source; an APMX receipt is evidence about the delivered
files, verified by another repository at merge.

**Together:** a gh-aw workflow could run an APMX factory: run-time safety
from gh-aw, merge-time evidence from APMX. That is a possible combination,
not a shipped integration.
