---
title: Write an Agent Contract
description: An Agent Contract is a reusable specification for agent work, one Markdown file with its inputs, outputs, skills, budget and checks.
---

An **Agent Contract** is a reusable specification for agent work: one Markdown
file that declares what the agent needs, which files it must produce, the
skills it imports, its budget, and the checks its output must pass. The
request changes from run to run; the contract stays.

## The six parts, and their real fields

The landing page uses plain words. Each one maps to a field in the file; the
labels are for people, the file keeps its own field names. The examples come
from the checkout factory's `build.contract.md`.

| Part | Field | Example |
| --- | --- | --- |
| Job description | Markdown body | "Implement the admitted checkout specification." |
| Skills | `imports:` (pinned in `apm.yml`) | `python-testing-patterns` |
| Assignment | `needs:` | `request.md`, `specification.md` |
| Deliverables | `produces:` | `changes.diff`, `implementation.md` |
| Budget | `budget:` | 3 attempts, 600 seconds |
| Checks | `verify:` | `shipping-examples`, `checkout-regression`, `implementation-report-format` |

The budget is the only limit APMX enforces: attempts and seconds. Allowed
actions or stop rules you write in the job description guide the agent; they
are not enforced fields.

## The smallest contract

From
[`examples/contracts/first-contract`](https://github.com/danielmeppiel/apmx/tree/main/examples/contracts/first-contract),
complete:

```markdown title="handoff.contract.md"
---
needs: notes.md
produces: handoff.json
verify:
  handoff: python3 checks/check_handoff.py handoff.json notes.md
---
Create handoff.json as a concise onboarding reference for a new teammate.

Read the facts and source IDs in notes.md. Write a JSON array containing one
object per source ID, with source_id, summary, and caution as nonempty strings.
Explain each fact plainly and name its important limitation. Do not invent
commands or facts.

Write the file, not just a chat response. Do not execute the commands described
in the notes, install anything, publish, or delegate work.
```

Preview first. Nothing executes or downloads:

```sh
apmx handoff.contract.md --on copilot --plan
```

Then run it. **Only run contracts you trust:** `--allow-host-access` lets the
agent, checks and package preparation use your host files, network and
logins. This is not a sandbox.

```sh
apmx handoff.contract.md --on copilot --allow-host-access
```

## Checks are your own commands

APMX runs each `verify:` command itself, against the saved output, outside
the agent's conversation. Exit 0 passes. Exit 1 is an ordinary rejection,
which a budget may retry. Exit 2 or any other failure stops the run. A
passing check proves only what that check tests.

## Add a budget

A contract runs once unless its frontmatter sets both budget fields:

```yaml
budget:
  max_attempts: 3
  max_seconds: 600
```

`max_attempts` is 1 to 16; `max_seconds` is shared across attempts and
checks. APMX retries only a complete delivery whose checks all ran with at
least one ordinary rejection. Producing the same rejected bytes again stops
as `no_progress`; a changing but still rejected candidate stops at
`max_attempts`. Every attempt starts from the same captured original project.
See [docs/repair.md](https://github.com/danielmeppiel/apmx/blob/main/docs/repair.md).

A real repair, from the build stage of the checkout factory:

```text title="2026-10-14, v0.5.0 release candidate, Copilot default model"
[3/5] build           needs request.md, specification.md -> produces changes.diff, implementation.md   budget 3 attempts
      attempt 1/3  agent 2m05s   checks: [x] shipping-examples [x] checkout-regression [+] implementation-report-format
      attempt 2/3  agent 2m11s   checks: [+] shipping-examples [+] checkout-regression [+] implementation-report-format
      [+] changes.diff, implementation.md -> handed to review, documentation
```

Attempt 1 also edited a file outside its scope, and two checks rejected it.
Attempt 2 passed. In that pre-release run the receipt export then failed
(exit 23), a bug fixed before v0.5.0 shipped.

## The real build contract

```yaml title="examples/contracts/software-factory/contracts/build.contract.md (frontmatter)"
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

## Share it as an APM package

Contracts, checks and skill pins travel as an APM package. `imports:` names
a skill; the version lives in `apm.yml`, and the APM lockfile pins the exact
content. The packaged-job example puts it this way: "The contract imports the
package name `handoff-style`; its dependency/version declaration belongs in
`apm.yml`, never in `imports`."

```yaml title="examples/contracts/software-factory/apm.yml"
name: checkout-factory
version: 0.1.0-dev
dependencies:
  apm:
    - git: wshobson/agents
      path: plugins/python-development/skills/python-testing-patterns
      ref: 46891e7e60da0e52baf1050b7b6391b64e84c6d9
```

Run a packaged factory straight from Git:

```sh
apmx --from 'danielmeppiel/apmx-demo-factory#v1.0.0' --plan
```

Next: [run a factory on any harness](../any-harness/).
