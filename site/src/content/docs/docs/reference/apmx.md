---
title: apmx
description: "Run an Agent Contract, a factory directory or a Git package: arguments, options, consent, outcomes and exit codes."
---

## Synopsis

```text
apmx [OPTIONS] [FACTORY_OR_CONTRACT_OR_PACKAGE]
apmx --from PACKAGE_REF [ENTRY] [OPTIONS]
```

Runs one contract, a local factory directory or an APM package from Git, and
writes a receipt for a COMPLETE run. If the work completed but writing the
receipt failed, it exits 23 with no receipt (see
[outcomes and exit codes](#outcomes-and-exit-codes)); the help text below
does not mention that case.

APMX has exactly three commands: `apmx` (run), [`apmx audit`](/apmx/docs/reference/audit/)
and [`apmx check`](/apmx/docs/reference/check/). There is no `apmx run`,
`apmx init`, `apmx install` or `apmx verify`, and no global `--json` flag.
`audit` and `check` are recognized only as the first argument: run a
directory with either name as `./audit` or `./check`.

<details>
<summary>Full <code>apmx --help</code> (v0.7.1)</summary>

The capture ran the binary under the name `apmx-v071`; the text is otherwise
verbatim and identical on main.

```text
Usage: apmx-v071 [OPTIONS] [FACTORY_OR_CONTRACT_OR_PACKAGE]

  Run a Git package, local factory directory or one .contract.md file. A
  factory's input and output files determine which steps run first.

  Packages default to the whole factory at their root. With --from, an
  optional entry selects a package-relative directory or .contract.md file.
  Package inputs and evidence belong to the calling directory.

  An existing positional directory is its own input, policy and evidence root.
  Use ./PATH to require a local directory; use --from to select a source
  package even when a local directory has the same name.

  Every COMPLETE run delivers a receipt. Verify the newest with 'apmx audit',
  or one with 'apmx audit RECEIPT' (see 'apmx audit --help'). 'apmx check
  PRODUCER_RECEIPT' re-checks another run's outputs here (see 'apmx check
  --help'). 'audit' and 'check' are reserved: run a directory with either name
  as ./audit or ./check.

Options:
  --from PACKAGE_REF       Run an APM source package against the current
                           project; default entry: package root.
  --on TEXT                Agent CLI to use (copilot, opencode).  [default:
                           copilot]
  --model MODEL            Model to use through the selected agent CLI.
  --plan                   Show steps and checks without running agents or
                           installing dependencies. A package not installed
                           here is fetched read-only into a temporary
                           workspace.
  --allow-unproven-inputs  For factories, permit fully checked native
                           handoffs; not certification. A single contract has
                           no handoffs, so it ignores this flag.
  --allow-host-access      Allow the selected agent CLI and checks to use host
                           files, network and available login details.
  -v, --verbose            Stream native debug logs and show source/process
                           details.
  --version                Show the version and exit.
  -h, --help               Show this message and exit.
```

</details>

## What to run

| Argument | Meaning |
| --- | --- |
| an existing directory | A local factory. It is also the root for its inputs, policy and evidence (`.apm/` is written there). Use `./PATH` to insist on a local directory. |
| a path ending in `.contract.md` | One contract. Inputs and evidence belong to the calling directory. |
| anything else | A Git package reference, `owner/repo[/path]#ref`. |
| `--from PACKAGE_REF` | Run that APM package against the current directory. Its default entry is the whole factory at the package root; a positional argument then selects a package-relative directory or `.contract.md`. A local path such as `--from ../factory` also works. |

With no argument and no `--from`, it is a usage error (exit 2):

```text title="apmx (v0.7.1)"
Usage: apmx-v071 [OPTIONS] [FACTORY_OR_CONTRACT_OR_PACKAGE]
Try 'apmx-v071 --help' for help.

Error: No factory selected. Pass a local directory, .contract.md file, Git package reference, or --from PACKAGE_REF.
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--from PACKAGE_REF` | | Run an APM source package against the current directory. |
| `--on TEXT` | `copilot` | The harness: `copilot` or `opencode` (exactly 1.2.24). `codex` is refused (`Runtime codex does not support native contracts.`); any other name gives `Unknown runtime: <name>`. Both are UNPROVEN, exit 21. |
| `--model MODEL` | the harness default | Passed to the harness. APMX never falls back to another model. |
| `--plan` | | Show the contracts, handoffs and checks without running agents or installing dependencies. A package not installed locally is fetched read-only into a temporary workspace. Exit 0, or 2 if the package cannot be fetched. |
| `--allow-unproven-inputs` | | Factories only: permit fully checked handoffs between contracts ("not certification"). A single contract ignores it. Required, with `--allow-host-access`, for unattended factory runs. |
| `--allow-host-access` | | Consent: the harness, APM and your checks may use host files, network and logins. |
| `-v`, `--verbose` | | Stream debug logs and process details. |
| `--version` | | Print `apmx, version 0.7.1`. Root command only. |
| `-h`, `--help` | | Show help. Works on all three commands. |

## Consent

A run is not sandboxed, so APMX asks before it starts:

- **A single contract never prompts.** Without `--allow-host-access` it stops
  with UNPROVEN (exit 21) and runs nothing.
- **A factory on an interactive terminal** (stdin and stdout are terminals
  and `CI` is unset) shows the plan and asks
  `Run these N contracts with <harness>? [y/N]`. The default is No. A package
  factory first asks to load the package.
- **Non-interactively** (`CI` set, or no terminal), a factory needs both
  `--allow-host-access` and `--allow-unproven-inputs`.

```text title="apmx --from ../factory, no consent (v0.7.1)"
[!] apmx: UNPROVEN
  Copilot and checks can read or change files, use the network, and use available login details. Run only contracts you trust. Add --allow-host-access to allow this run; policy still applies. Use --plan to preview without running.
```

## Where it refuses to run

A run, including `--plan`, stops with UNPROVEN (exit 21) when:

- the calling directory is inside a Git repository that has **any remote**;
- the calling directory's `apm.yml` has a `policy` key;
- `APM_POLICY_DISABLE=1` or `APM_NO_SCRIPTS` is set.

```text title="apmx --from ../factory --plan, inside a clone with a remote (v0.7.1)"
[!] apmx: UNPROVEN
  Native contracts require positively established no-policy governance. Remote governance cannot be established offline. Configured, disabled and unresolved governance are unsupported.
```

Run from a copy outside the repository. `run.yml` does this for you: it
stages the factory outside the Git checkout.

## Preview (--plan)

```text title="apmx --from danielmeppiel/apmx-demo-factory#v1.0.0 --plan (v0.7.1)"
Factory  danielmeppiel/apmx-demo-factory#v1.0.0   5 contracts   copilot / default model

[1/5] planning        needs request.md -> produces plan.md
                      checks: plan-sections   1 attempt
[2/5] specification   needs request.md, plan.md* -> produces specification.md
                      checks: specification-format-references   1 attempt
[3/5] build           needs request.md, specification.md* -> produces changes.diff, implementation.md
                      checks: shipping-examples, checkout-regression, implementation-report-format   budget 3 attempts, 600s
[4/5] documentation   needs request.md, specification.md*, changes.diff*, implementation.md* -> produces documentation.diff, documentation.md
                      checks: documentation-format-links-examples, documented-checkout, documentation-report-format   budget 2 attempts, 300s
[5/5] review          needs request.md, specification.md*, changes.diff*, implementation.md*, documentation.diff*, documentation.md* -> produces review.md
                      checks: review-format-references   1 attempt
                      * from an earlier contract

Nothing ran. Run it with:  apmx --from 'danielmeppiel/apmx-demo-factory#v1.0.0'
Unattended:                apmx --from 'danielmeppiel/apmx-demo-factory#v1.0.0' --allow-host-access --allow-unproven-inputs
Runs are local and not sandboxed; APMX does not cap model charges.
```

- Each stage shows its inputs (`needs`), outputs (`produces`), checks and
  attempts: `1 attempt` without a budget, `budget N attempts, Ss` with one.
- `*` marks a file an earlier contract produces.
- The footer prints the command to run it, the unattended form, and the
  limits.

## Where results go

Results are written under `.apm/` in the root: the factory directory for a
local factory, or the calling directory for a contract or `--from`.

| Run | Directory |
| --- | --- |
| a factory | `.apm/chains/<id>/` |
| a single contract with a `budget` | `.apm/controllers/<id>/` |
| a single contract, one attempt | `.apm/runs/<id>/` |

`<id>` is `YYYYMMDDTHHMMSSZ-<12 hex>`, for example
`20261022T180938Z-c5a4aaff9823`. Inside it:

| Path | Content |
| --- | --- |
| `artifacts/` | every stage's declared outputs; what the run's `Outputs` line points at |
| `receipt.intoto.jsonl` | the receipt (COMPLETE runs only) |
| `outputs/` | the delivered files, at their project paths |
| `evidence/sha256/` | content-addressed evidence; required by `apmx check` and the trusted signer, optional for a structural `apmx audit` |
| `record.json` | the run record (not part of the receipt) |

Agents work on private copies of the project; your files are not modified by
the run. Receipts are never overwritten.

A COMPLETE run ends like this (from `apmx check`, which uses the same run
pipeline):

```text title="End of a COMPLETE run (v0.7.1, abridged)"
[+] COMPLETE   1/1 contract   1/1 check   2.4s

Outputs   .apm/chains/20261022T180934Z-49ff1100dc06/artifacts/
          handoff.json
Receipt   .apm/chains/20261022T180934Z-49ff1100dc06/receipt.intoto.jsonl
          provenance  in-toto + SLSA v1
          checks      in-toto test-result (1)
          inventory   CycloneDX 1.5 (1 component)
          outputs     1 delivered file in outputs/
          unsigned: binds content, not identity; review before sharing

Next      apmx audit .apm/chains/20261022T180934Z-49ff1100dc06/receipt.intoto.jsonl
```

## Outcomes and exit codes

| Exit | Outcome | Meaning |
| --- | --- | --- |
| 0 | COMPLETE | Every declared output retained, every required check passed, evidence finalized. |
| 20 | REJECTED | At least one check exited 1. |
| 21 | UNPROVEN | Missing consent, refused by the policy prerequisite, unsupported harness or field, or incomplete checks (a check exiting with anything other than 0 or 1, or not finishing). |
| 22 | HALTED | Execution, cancellation or finalization stopped the run; also an invalid contract source. |
| 23 | (COMPLETE) | The run completed but writing the receipt failed. Retry the export, not the model: see [export again without inference](https://github.com/danielmeppiel/apmx/blob/main/docs/evidence.md#export-again-without-inference). |
| 2 | usage error | Bad arguments; `--plan` with a package that cannot be fetched. |

COMPLETE is not trust, certification, correctness or permission to merge.

## Environment variables

| Variable | Effect |
| --- | --- |
| `COPILOT_GITHUB_TOKEN` | The Copilot CLI token. `run.yml` passes its `agent-token` secret as this, in the agent's job only. |
| `CI` | Non-empty disables interactive confirmation. |
| `APMX_APM_BACKEND` | Source installs only: absolute path to the pinned APM executable. The native bundle ignores it and uses `libexec/apm/apm`. |
| `APM_POLICY_DISABLE=1`, `APM_NO_SCRIPTS` | Make runs refuse (see above). |
| `GITHUB_APM_PAT`, `ADO_APM_PAT`, `GITLAB_APM_PAT`, `GITHUB_HOST`, TLS variables | Inherited APM package-access behavior. |

`COPILOT_ALLOW_*`, `COPILOT_ASSISTED_APPROVAL`, `COPILOT_SKIP_PERMISSIONS`
and `COPILOT_YOLO` are removed from the Copilot CLI's environment.

## Limits

- **Not sandboxed.** Every run record says `"isolation": "unavailable"`. The
  agent and your checks can use your host files, network and logins.
- **No cost cap.** Records carry `spend_cap: "unavailable"`; a budget limits
  attempts and time only.
- **Harness and model are recorded, not enforced.**
