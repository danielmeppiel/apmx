---
title: Agentic software factories
description: A factory is a set of agents that turns intent into an outcome. In APMX it is a directory of Agent Contracts and checks.
---

An agentic software factory is a set of agents that turns intent into an
outcome, usually software.

You write down what you want: your intent. The factory's agents work on it
step by step, each against its own checks. When every check passes, you get
the result and a receipt of how it was made.

## In APMX: contracts that hand off through files

A factory is a directory, or an APM package, of
[Agent Contracts](/apmx/docs/agent-contracts/) (`*.contract.md` files) and the
checks they name (usually under `checks/`). There is no pipeline file.

APMX finds every contract in the directory and infers the order by matching
each contract's `needs` to another contract's `produces`, not by file names. A
contract that `needs: plan.md` runs after the one that `produces: plan.md`,
and it receives that file only after the producing contract's checks passed.

The demo factory, simplified from its plan (later contracts also read the
earlier files):

```text
request.md
  -> planning       -> plan.md
  -> specification  -> specification.md
  -> build          -> changes.diff, implementation.md
  -> documentation  -> documentation.diff, documentation.md
  -> review         -> review.md
```

See the full plan, with every handoff and check, in
[Your first run](/apmx/docs/quickstart/#preview-a-real-factory).

## Your intent

In the examples, the intent lives in `request.md`: a plain file the
contracts list in `needs`. To APMX it is an ordinary input, not a special
name; the receipt records it by digest. In CI, `run.yml`'s `request` input
writes the file for you (its `request-file` defaults to `request.md`).

## The factory definition digest

APMX computes one SHA-256 over the factory definition: contracts, checks,
checker files, skill pins, budgets, limits and dependencies. It does not
include the harness, the model, clocks, paths or the package label.

- The same factory has the same digest on GitHub Copilot CLI and on
  OpenCode. That is the **Portability** promise.
- Your policy approves a digest, not a name (`factories.allow`). That is the
  **Trust** promise.

The audit's Factory row shows it as `definition sha256:0bec0549b9af..`.

## Share it as an APM package

Contracts, checks and skill pins travel as an APM package. `apm.yml` pins
each dependency; a contract's `imports` names the installed package to use.
The demo factory's `apm.yml`:

```yaml title="apm.yml (apmx-demo-factory)"
name: checkout-factory
version: 1.0.0
dependencies:
  apm:
    - git: wshobson/agents
      path: plugins/python-development/skills/python-testing-patterns
      ref: 46891e7e60da0e52baf1050b7b6391b64e84c6d9
```

Run a package straight from Git with `apmx --from owner/repo[/path]#ref`.
APM 0.33.0 is bundled with APMX; you do not install it.

## Where a factory runs

| | On your machine (`apmx`) | In CI (`run.yml`) |
| --- | --- | --- |
| Agent, checks, signer | one machine, no separation | three isolated jobs, each on a fresh runner |
| Sandbox | no | no; each job gets a fresh runner |
| Receipt | unsigned | unsigned receipt of record, plus GitHub attestations from a trusted signer: a verification summary binds every receipt line and delivered file by digest |

:::caution[What a factory run is not]
- **Not sandboxed.** The agent and your checks can use your host files,
  network and logins.
- **COMPLETE is not correctness.** It means every declared output exists and
  every check passed. It is not permission to merge.
- **A budget does not cap cost.** Today attempts and time are enforced;
  model charges are not capped.
:::

Next: [Agent Contracts](/apmx/docs/agent-contracts/), then
[run a factory in CI](/apmx/docs/run-in-ci/).
