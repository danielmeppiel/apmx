# apmx

Run **one explicit contract** through native GitHub Copilot, retain its output,
and assess that exact output using independently captured checks.

This private standalone project does not require APM or experimental activation.
It is derived from Microsoft's MIT-licensed APM contract engine; see
[source origin and migration boundaries](docs/source-origin.md).

## Run

Install native GitHub Copilot CLI and Git. Authenticate with Copilot itself.
Install any tools explicitly required by the selected contract's checks.
Windows requires native `copilot.exe`, not an npm `.cmd` shim.

```sh
apmx job.contract.md --on copilot --plan
apmx job.contract.md --on copilot --model YOUR_MODEL --allow-host-access
apmx --from ./trusted-package contracts/handoff.contract.md --on copilot --allow-host-access
```

`--plan` only inspects local inputs and installed package/lock state. It neither
fetches packages nor probes/launches Copilot. Remote execution also accepts an
explicit HTTPS/SSH Git package reference with a literal revision. An existing
direct caller lock is replayed exactly; drift is refused rather than repaired.
One self-contained root `SKILL.md` dependency may be imported as context.

**Run only contracts you trust.** `--allow-host-access` permits the native producer
and checks to use host files, network and available login details. Native tool
restrictions are not a sandbox. Policy admission remains fail-closed: configured,
disabled or unresolved remote governance is unsupported.

| Exit | Meaning |
|---|---|
| 0 | Help, version, or planning completed; **not** verification success |
| 20 | REJECTED: a failed independent check |
| 21 | UNPROVEN: includes passing checks without certified isolation, missing output, incomplete checks, and unsupported assurance/policy requests |
| 22 | HALTED: operational failure, cancellation or unconfirmed cleanup |

Evidence belongs to the calling directory, under `.apm/runs/<run-id>/`.
Checks run in separate workspaces from immutable captured inputs/resources,
against the retained output digest. Producer exit zero never certifies a result.
Verbose `-v` adds observations; only public native commentary is streamed.
Redaction is best-effort, not a promise that arbitrary output cannot contain
sensitive content.

## Contract

```markdown
---
needs: notes.md
produces: handoff.json
verify:
  valid: 'python3 -I checks/check_handoff.py'
---
Read notes.md and create handoff.json.
```

Inputs are caller-relative; packaged contracts/checks are package-relative.
Select an explicit `.contract.md` file; no default job or script fallback exists.
`examples/contracts/packaged-job` includes a caller, package and imported skill.
On Windows use a direct native checker executable with POSIX-style quoting and
forward slashes, for example
`'"C:/Program Files/Python312/python.exe" -I checks/check_handoff.py'`.
Windows shell pipelines and batch shims are not supported.

## Develop

Python 3.12 is the development baseline; binary archives bundle their Python
runtime, but not Copilot, Git, or contract-specific check tools.

```sh
uv sync --extra dev --extra build
uv run pytest
uv run apmx --help
```

Dependencies resolve from public PyPI. Protocol fixtures need no AI credentials
and are not live model inference. The release pipeline independently checks
downloaded archive bytes on matching platforms; fixture results do not establish
publisher signing or a sandbox.
Run one contract with native Copilot; retain the output and independent check results.
