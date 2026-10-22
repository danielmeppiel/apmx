---
title: Choose your harness and model
description: Run the same factory on GitHub Copilot CLI or OpenCode 1.2.24, with your choice of model. What is recorded, and what is not enforced.
---

Same factory, skills and checks on your choice of harness and model. Today
that means GitHub Copilot CLI (the default) and OpenCode 1.2.24.

A harness is the agent CLI that runs each contract. You pick it with `--on`
and the model with `--model`; the factory does not change.

## Pick the harness

```sh
apmx ./my-factory --on copilot     # the default
apmx ./my-factory --on opencode    # OpenCode, exactly version 1.2.24
```

OpenCode must be exactly 1.2.24; other versions refuse to run. Any other
harness name is refused with UNPROVEN (exit 21):

```text title="Refused harness names (main, trimmed: two of several runs, exit lines omitted; v0.7.1 prints the same for codex)"
$ apmx --from ../factory --plan --on codex
[!] apmx: UNPROVEN
  Runtime codex does not support native contracts.

$ apmx --from ../factory --plan --on claude
[!] apmx: UNPROVEN
  Unknown runtime: claude
```

## Pick the model

`--model MODEL` is passed to the harness as is. Without it, the harness uses
its own default. APMX never falls back to another model.

```sh
apmx ./my-factory --on opencode --model MODEL
```

## Same factory, same digest

The factory definition digest covers contracts, checks, skill pins, budgets
and dependencies, but not the harness or the model. So the same factory has
the same digest on both harnesses, and a policy that approves it approves it
on both. Only the plan's header and hints change:

```text title="apmx --from ../factory --plan --on opencode (v0.7.1, abridged)"
Factory  ../factory   5 contracts   opencode / default model
...
Nothing ran. Run it with:  apmx --from ../factory --on opencode
Unattended:                apmx --from ../factory --on opencode --allow-host-access --allow-unproven-inputs
Runs are local and not sandboxed; APMX does not cap model charges.
```

## Recorded, not enforced

Each stage records the harness and models it reported. No policy enforces
them: the `factories` policy has no harness or model rule.

Per contract, the receipt keeps `harness`, `harnessVersion`,
`requestedModel` and `observedModels`. Print them with
`apmx audit --explain stages`:

```text title="apmx audit --explain stages (v0.7.1, same on main): a five-contract Copilot run"
Stages
  planning         copilot  requested default  observed gpt-6.1-sol  attempts 1
  specification    copilot  requested default  observed gpt-6.1-sol  attempts 1
  build            copilot  requested default  observed gpt-6.1-sol  attempts 1
  documentation    copilot  requested default  observed gpt-6.1-sol  attempts 1
  review           copilot  requested default  observed gpt-6.1-sol  attempts 1
```

In a receipt of record from CI, these are what the agent's job **reported**;
the job that reruns the checks does not observe the model:

```text title="apmx audit --explain stages (v0.7.1, same on main): a CI receipt of record"
Stages
  handoff          copilot 1.0  requested default  observed fixture-model  attempts 1
                   harness and models reported by the producer; checks rerun by apmx check (replay)
```

OpenCode may report no model, so `observedModels` can be empty. The
repository's [OpenCode guide](https://github.com/danielmeppiel/apmx/blob/main/docs/opencode.md)
covers that profile in detail.

## In CI

`run.yml` takes a `harness` input (default `copilot`) and a `model` input
(empty means the harness default). `run.yml` installs only the Copilot CLI;
it does not install OpenCode. See
[run.yml and actions](/apmx/docs/reference/run-yml/#inputs).

## What the harness may do

APMX starts the Copilot CLI with a narrowed tool set: `view`, `apply_patch`,
APMX's private artifact tools (`write_file`, `delete_file`,
`export_changes`), and `skill` when the contract imports skills. Shell and URL
tools are denied, built-in MCP servers and custom instructions are disabled,
and broad-approval environment variables are removed.

This restricts the harness's tools. It is not an operating-system sandbox:
the run can still use your host's files, network and logins.

Next: every option of the [`apmx` command](/apmx/docs/reference/apmx/).
