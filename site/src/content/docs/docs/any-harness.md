---
title: Run a factory on any harness
description: Chain contracts into a factory, preview it, run it on GitHub Copilot CLI or OpenCode, and read the outcome literally.
---

A **factory** is a directory of contracts and checks. Contracts hand off
through files: one that `needs: plan.md` waits for the one that
`produces: plan.md` and receives that exact checked file. No pipeline file
is needed. The factory starts from your intent, written in `request.md`. The checkout example adds free delivery from a 5000-cent subtotal:

```text
request.md -> plan.md -> specification.md
                        -> changes.diff + implementation.md
                        -> documentation.diff + documentation.md -> review.md
```

## Three commands

```sh
apmx ./feature-factory --plan     # see the contracts, handoffs and checks; no agents, no installs
apmx ./feature-factory            # run: each contract loops until its checks pass
apmx audit <receipt>              # verify what was produced, from what, and how
```

The same commands work for a single contract (`apmx build.contract.md`) or a
Git package (`apmx --from owner/repo`). `--on copilot|opencode` selects the
harness; Copilot is the default.

## Preview

```text title="apmx --from 'danielmeppiel/apmx-demo-factory#v1.0.0' --plan"
Factory  danielmeppiel/apmx-demo-factory#v1.0.0   5 contracts   copilot / default model

[1/5] planning        needs request.md -> produces plan.md
                      checks: plan-sections   1 attempt
[2/5] specification   needs request.md, plan.md* -> produces specification.md
                      checks: specification-format-references   1 attempt
[3/5] build           needs request.md, specification.md* -> produces changes.diff, implementation.md
                      checks: shipping-examples, checkout-regression, implementation-report-format   budget 3 attempts, 600s
```

(Abridged; `*` marks an earlier contract's checked output.)

## Switch the harness, keep the factory

```sh
apmx --from 'danielmeppiel/apmx-demo-factory#v1.0.0' --on opencode --model github-copilot/gpt-5.6-sol
```

The OpenCode profile (OpenCode 1.2.24) uses the same preparation, capture,
checking and repair as Copilot; only the native invocation adapter differs.
APMX never switches providers or falls back to another model after an error.
See [docs/opencode.md](https://github.com/danielmeppiel/apmx/blob/main/docs/opencode.md).

Observed in development: one factory definition (`sha256:0bec0549...`) ran on
GitHub Copilot CLI (observed model `gpt-6.1-sol`) and on OpenCode (requested
`github-copilot/gpt-5.6-sol`). The code came out different (`src/pricing.py`
`e6981e53...` vs `41d1739a...`). Both passed the same nine checks.

:::note
Each stage records which harness ran it, the version it reported and the
models it reported. These are recorded, not enforced: no policy selects
models or harnesses yet ([#39](https://github.com/danielmeppiel/apmx/issues/39)).
OpenCode does not report a model.
:::

## Read the outcome literally

| Outcome | Exit | Meaning |
| --- | --- | --- |
| COMPLETE | 0 | Every declared output retained and every required check passed. |
| REJECTED | 20 | At least one required check failed. |
| UNPROVEN | 21 | Incomplete output or checks, missing consent or policy. Never success. |
| HALTED | 22 | Execution, cancellation or finalization stopped the run. |
| (COMPLETE) | 23 | The run completed but writing its receipt failed. Retry the export, not the model. |

COMPLETE is not a sandbox, a correctness guarantee or permission to merge.
See [docs/results.md](https://github.com/danielmeppiel/apmx/blob/main/docs/results.md).

Next: [read the receipt](../receipt/).
