# Write your first contract

Turn three source notes into a JSON summary for a new teammate.
This example shows one Copilot task, one output file and one check.

For a feature-to-review workflow instead, see the
[software-factory example](../software-factory/README.md).

## Set up the example

Use the [installation guide](../../../docs/install.md) to put APMX on PATH.
For a prebuilt installation, complete its checker setup to select a separate
Python environment; the source fallback prepares its own environment.
You also need Git, GitHub Copilot CLI and Python 3.12 or newer. Authenticate
with native Copilot before live execution. Use the example files from that
same pinned example checkout.

This example selects one contract file. To connect several steps, select a
factory directory instead, as shown in the software-factory walkthrough.
Use example files matching your runtime, as explained in the installation guide.
The native bundle includes its APM backend; the source route provisions it.
Neither route needs a global APM installation.

On macOS or Linux, start at the source checkout's root. Copy the supplied
example to a new folder outside any Git repository:

```sh
mkdir "$HOME/apmx-first-contract-demo" &&
cp -R examples/contracts/first-contract/. "$HOME/apmx-first-contract-demo/" &&
cd "$HOME/apmx-first-contract-demo"
```

The destination must not already exist. If you have used that name before,
choose another unused name in all three commands; do not overwrite old results.
If your home directory is itself in a Git repository, choose another location.
Do not remove a real project's remotes or policy to make it eligible.

On Windows, follow the
[PowerShell first-run walkthrough](../../../docs/install.md#windows-first-run)
instead. It prepares a fresh copy and adjusts the checker command for the
selected native Python environment without changing the source checkout.

## Preview before executing

From your copied example folder:

```sh
apmx handoff.contract.md --on copilot --plan
```

The preview lists `notes.md`, `handoff.json` and the `handoff` check.
Nothing executes or downloads. Copilot must be discoverable, but the preview
does not validate its login or run the Python checker.

## Run the task

Continue only after the preview succeeds.

**Only run contracts you trust.** `--allow-host-access` permits Copilot,
checks and package preparation to use your host files, network and available
login details. This is not a sandbox.

```sh
apmx handoff.contract.md --on copilot --allow-host-access
```

This preserves your configured Copilot model and may incur usage charges.
Use `--model MODEL` only if you want to select a model explicitly.

With current v0.4.0 source and downloads, the important result lines from a
completed run look like this. Output is abbreviated and `<run-id>` is a
placeholder:

```text
Contract 1/1: handoff
  Produces: handoff.json
  Checks:
    [+] PASS handoff

[+] Contract COMPLETE
  Contract: 1/1 completed
  Check: 1/1 passed
Evidence:
  Artifacts: 1 file retained
  Directory: .apm/runs/<run-id>/artifacts
  Record: .apm/runs/<run-id>/record.json
```

**Open the printed Evidence directory and Record paths.** They are relative to the folder
where you ran the command. The output is saved under `.apm/runs/`, not copied
over a `handoff.json` at the top of that folder.

The checker validates the JSON format and confirms there is one entry for
each source ID. It does not establish that every summary is factually correct.
Read the summaries and cautions yourself.

**Historical v0.3.2 downloads and the source pin still exit 21 even with passing
checks.** Current v0.4.0 source and downloads exit 0 only after complete
evidence finalization. Neither result means sandboxing. Missing output or
incomplete checks still produce nonzero results. Read the check results and versioned record.
See [result meanings](../README.md#read-outcomes-literally) for other outcomes.

## Read the contract

A contract is a short Markdown file describing a step's inputs, output and
checks, followed by instructions for Copilot. This example uses the supplied
[notes](notes.md) and [checker](checks/check_handoff.py):

```markdown
---
needs: notes.md
produces: handoff.json
verify:
  handoff: python3 checks/check_handoff.py handoff.json notes.md
---
Read notes.md and write a JSON array to handoff.json.
Include one object per source ID, with nonempty source_id, summary and caution.
Explain each fact plainly and name its limitation. Do not invent facts.
```

| Part | Meaning |
| --- | --- |
| `needs` | Input files from the folder where you run APMX |
| `produces` | The one output file to save and check |
| `verify` | Named checker commands; here, both the output and source notes are arguments |
| Instructions | The work Copilot should do |

APMX runs checks separately against the saved output. The contract's author
chooses those checks: a passing check proves only what that check tests.
The complete runnable file is [handoff.contract.md](handoff.contract.md).

## Next steps

- [Connect four contracts](../software-factory/README.md) from a feature request to five checked artifacts.
- [Reuse a packaged task](../packaged-job/README.md) with bundled APM.
- [Return to the overview](../../../README.md).
