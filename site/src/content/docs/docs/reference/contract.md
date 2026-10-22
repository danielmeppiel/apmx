---
title: Contract file and factory layout
description: Fields, limits and validation of *.contract.md, how a directory becomes a factory, and how skills are pinned.
---

## File shape

A contract is one file whose name ends in `.contract.md`: YAML frontmatter,
then a non-empty Markdown body. The whole file is at most 256 KiB.

```markdown title="build.contract.md (shape)"
---
needs: [...]
produces: [...]
imports: [...]
budget: { max_attempts: 3, max_seconds: 600 }
verify: { check-name: command }
---
The Role: how the agent works, every time.
```

## Fields

The labels are for people; the file keeps its field names. Grouped as the
contract reads: in (Assignment), how it works (Role, Dependencies, Budget),
out (Checks, Deliverables).

| Label | Field | Required | Type and limits | Meaning |
| --- | --- | --- | --- | --- |
| **Assignment** | `needs` | no (default none) | One path or a list of up to 16 fixed relative paths. No globs; nothing under `.git`, `.apm` or `apm_modules`; no case collisions. | Your intent, each time: the files the agent starts from. Your intent (`request.md` in the examples, by convention) and checked files from earlier contracts. A need another contract produces becomes a handoff. |
| **Role** | Markdown body | yes | Non-empty text. | How it works, every time: behavior and standards, constant on every run. Sent to the agent. |
| **Dependencies** | `imports` | no | A list of up to 256 names of installed APM packages. Not paths, not versions; no duplicates, compared without case. | What it can use. APMX activates only skills and instructions from these packages (see [below](#dependencies-imports)). |
| **Budget** | `budget` | no | Exactly `max_attempts` (integer, 1 to 16) and `max_seconds` (more than 0, at most 86400). | What the job may spend; today attempts and time are enforced, cost is not capped. |
| **Checks** | `verify` | yes | A mapping of 1 to 8 checks: name to a non-empty command string. | How the work is judged. Run by APMX, outside the agent. |
| **Deliverables** | `produces` | yes | One path or a list of 1 to 16 non-overlapping fixed relative paths. | What it hands back: the files the agent must write. |

Two keys are refused, and the run is UNPROVEN:

```text
run is unsupported by the native agent-only contract profile.
sandbox is unsupported by the native agent-only contract profile.
```

Any other key is an error: `Unknown contract field: <key>.`

## Check commands

- **Names** match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
- **Commands** run on the host, from `PATH` (for example `python3`), outside
  the agent's conversation.
- **Time:** each check may run for 180 seconds.
- **Exit codes:**

| Exit | Meaning |
| --- | --- |
| 0 | pass |
| 1 | reject: REJECTED, retryable under a budget |
| anything else, a timeout, or not cleaned up | not a pass: UNPROVEN, not retried |

## Budget

Without `budget`: one attempt, at most 1200 seconds per attempt and 180
seconds per check.

With `budget`:

- Up to `max_attempts` attempts share one `max_seconds` deadline.
- Only a REJECTED candidate (a check exited 1) is retried. Each retry gets the
  previous checks' exit codes and a transcript tail.
- The run stops with `no_progress` when an attempt produces the same rejected
  bytes again, `max_attempts` when the count is reached, and
  `budget_deadline` at the deadline.

Cost is not capped: run records carry `spend_cap: "unavailable"`.

## Output limits

Each declared output may be at most 4 MiB, and all outputs together at most
16 MiB.

## Factory layout

A factory is a directory. APMX finds every `*.contract.md` under it,
recursively, and skips `.git`, `.apm`, `apm_modules`, `.venv`, `venv`,
`node_modules`, `__pycache__`, `.agents`, `.github`, `.claude`, `.copilot`,
`.codex`, `.cursor`, `.opencode`, `vendor`, `dist`, `build` and caches.

- At most 64 contracts.
- Symlinks are refused.
- Order and handoffs come from the file names in `needs` and `produces`.
  There is no pipeline file.

## Dependencies (imports)

Each `imports` entry names one installed APM package. `apm.yml` declares and
pins it; the APM lock fixes its exact content. An import that matches no
installed package, or more than one, is an error, and imports need an
installed APM lock.

From each imported package, APMX activates only:

- **skills**: `SKILL.md` files, with `name` and `description` in their
  frontmatter;
- **instructions**: `.apm/instructions/*.instructions.md` files.

Nothing else in the package reaches the agent through the contract. Two cases
are rejected with `unsupported_import`:

- a selected skill or instruction whose frontmatter declares `hooks`, `mcp`,
  `mcpServers`, `lspServers`, `agent`, `model`, `allowed-tools` or `context`;
- a scoped instruction: `applyTo` must be absent, `"*"`, `"**"` or `"**/*"`.

The demo factory pins one skill package:

```yaml title="apm.yml (apmx-demo-factory)"
name: checkout-factory
version: 1.0.0
dependencies:
  apm:
    - git: wshobson/agents
      path: plugins/python-development/skills/python-testing-patterns
      ref: 46891e7e60da0e52baf1050b7b6391b64e84c6d9
```

A factory published as a package is referenced as `owner/repo[/path]#ref`,
for example `danielmeppiel/apmx-demo-factory#v1.0.0`. APM 0.33.0 is bundled
and pinned; there is no separate APM install.

## Examples

```markdown title="examples/contracts/first-contract/handoff.contract.md (frontmatter)"
---
needs: notes.md
produces: handoff.json
verify:
  handoff: python3 checks/check_handoff.py handoff.json notes.md
---
```

```markdown title="examples/contracts/software-factory/contracts/build.contract.md (frontmatter)"
---
needs:
  - request.md
  - specification.md
produces:
  - changes.diff
  - implementation.md
imports:
  - python-testing-patterns
budget:
  max_attempts: 3
  max_seconds: 600
verify:
  shipping-examples: python3 -I -B checks/acceptance.py changes.diff
  checkout-regression: python3 -I -B checks/regression.py changes.diff
  implementation-report-format: python3 -I -B checks/documents.py implementation implementation.md
---
```

Concepts: [Agent Contracts](/apmx/docs/agent-contracts/) and
[factories](/apmx/docs/factories/).
