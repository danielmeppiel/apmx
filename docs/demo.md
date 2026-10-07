# A clean two-harness demo

This macOS/Linux kit separates machine preparation from the presentation.
It uses a genuine PyInstaller APMX bundle, its official APM backend and the
unchanged five-stage factory. It does not simulate an agent, grant consent,
apply patches automatically, or change global shell/login configuration.

## Present

From the prepared kit directory, open the Copilot terminal:

```sh
./demo copilot
```

The new shell starts inside the application with the native `apmx` and checker
Python on PATH. Show `request.md` and `../factory/contracts/build.contract.md`
in your editor.

**"Make delivery free from fifty dollars. Here are the deliverables and checks."**

```sh
apmx --from ../factory --plan
```

Expect five stages, seven outputs and nine checks. Preview performs no model
calls. The application is the implicit workspace; source files need no inventory
in the contract. The factory supplies contracts, checks and the pinned testing
capability. The package root is the default entrypoint; Copilot is the default
harness. Preview is optional: the execution command also displays the factory
before asking for execution consent.

**"Let Copilot operate this factory."**

```sh
apmx --from ../factory --verbose
```

First approve loading the factory and installing its dependencies with APM in a
temporary workspace. Inspect the displayed inputs, outputs and checks, then
approve execution. Both prompts default to **No**. Loading approval alone never
starts an agent. Native agents and checkers can
access host files, network and available logins; this is not a sandbox. Model
usage can cost money. Copilot keeps its configured model. The helper supplies
neither model overrides nor permission flags.

Public agent activity and checker stdout/stderr stream by default. `--verbose`
also mirrors full native debug diagnostics, with safety bounds; omit it for a
more readable presentation without hiding the actual work. Inspect debug output
before projecting it: native metadata can be sensitive. `Found input` means a
file was actually captured, not merely declared. Narrate the check command,
its output and then its authoritative result, in that order.

After successful execution and automatic evidence delivery, show the printed
code patch and separate documentation patch. Required checks assess their stated
scope; advisory review is not certification. The factory retains changes rather
than applying them to the original application. Never treat exit 21 or
evidence-delivery exit 23 as a successful demo.

**"The patch is only half the delivery. Now show me what it is bound to."**

```sh
demo proof
```

Show the actual factory-definition SHA-256 and the same reference inside the
SLSA v1 predicate, wrapped in an in-toto Statement v1. Then show the output
subjects and their SHA-256 digests, the official APM CycloneDX 1.5 ABOM and the
per-producer/per-check statement counts. The ABOM is an inventory byproduct,
not a claim that the model read the inventory. Open `provenance.intoto.json`,
`abom.cdx.json` and one `checks/*.intoto.json` in the printed evidence directory
when you want to show the real standard documents, not a slide about them.

**"Do not take APMX's word for it. Use a separate consumer."**

```sh
demo verify
demo verify --controls
```

The first command uses the existing independent Python consumer and cached
upstream schemas: no APMX import, model call or network fetch. It validates the
formats, actual file bytes, definition bindings, output subjects and checker
relationships. The second relocates the package and tests corruptions on
disposable copies, including semantic errors with refreshed index hashes.
The original evidence stays unchanged.

**"These bytes are bound to this recorded factory, these inputs and these checks.
Change an artifact or break a relationship, and independent verification rejects it.
This is unsigned local evidence: it does not authenticate who operated the factory
or establish a SLSA security level."**

The helper always prints the selected run ID. By default it selects the newest
recorded factory attempt in this active application, never an older success
behind a newer incomplete run and never archived history. A command that fails
before creating a run has no new record; do not misrepresent an older record
as that command's result. Use `--run ID` for deliberate historical selection.
From the kit root, use `./demo proof copilot` or `./demo verify copilot`.

**"Change the harness, not the factory."**

Open a second terminal in the kit directory:

```sh
./demo opencode
```

Then run:

```sh
apmx --from ../factory --on opencode --model github-copilot/gpt-5.6-sol --verbose
demo proof
demo verify
```

This is the explicitly tested OpenCode provider/model selection. It requires
your own model access; the helper does not change global OpenCode configuration.
Both applications start from the same seed and use the same factory.

Show the second run's code/docs outputs, actual standard documents and independent
verification. Compare the two printed factory-definition hashes. Matching definition
fingerprints establish the same contracts/checks/capabilities/budget, not identical
generated code. Evidence is unsigned, not authenticated attestation.

For a tight talk, run one harness live and label the other result as a prepared
run. Do not promise bounded whole-factory latency. Only the build declares three
attempts/600 shared seconds; the other stages remain single-attempt. Narrate
repair only if it actually occurs.

## Reset and relaunch

Keep runs in the foreground. Finish or cancel them normally, then type `exit`
in **both** demo shells before resetting from the kit directory:

```sh
./demo reset
./demo copilot
```

Reset moves the old applications, edits and `.apm/` evidence into a unique
`history/` directory, then prepares clean consumers through official APM and
previews both harnesses. It may access the network; it makes no model calls.
Reset just one application with `./demo reset copilot` or `./demo reset opencode`.
Portable evidence remains inspectable after this move. Raw canonical records
retain their original absolute paths and bytes; do not rewrite them or treat
the archived directory as a resumable execution.

To clear the active applications without immediately preparing replacements:

```sh
./demo clean
./demo status
./demo reset
```

Clean also archives rather than deletes. History is never automatically pruned;
review and remove individual archived generations yourself when no longer needed.
Keep this private: source, logs and retained evidence can contain project data.
An active helper shell blocks reset/clean. Independently launched or background
processes are outside that lease: stop them yourself before maintenance.

`./demo status` verifies the kit and lists readiness. It does not certify login,
model access or a successful inference run. A failed preparation leaves its log
under `.demo/logs/`, preserves previous runs, and blocks entry until reset succeeds.
Do not move the kit, replace its factory, edit its original seed or substitute
executables; prepare a new kit for another definition or binary.

Kit-local links, launcher discovery and the consumer's `../factory` dependency
are relative. The checker Python is an external machine prerequisite, not a
relocatable virtual environment. Ownership metadata and historical canonical
records deliberately retain their original absolute locations. To relocate,
prepare a fresh destination and preserve the old kit as an archived generation;
do not rewrite recorded evidence or reuse stale installed dependency state.

## Prepare once, backstage

Keep the tooling here in the APMX repository, beside the existing factory and
release builder. There is no separate demo repository or duplicate factory.
Prepared applications and run data belong outside Git and must not be committed.

Requirements: Git, authenticated Copilot/OpenCode CLIs, Bash, a checker Python
3.12+ environment containing the exact `factory` extra from `pyproject.toml`,
an independent verifier environment and pinned schema cache prepared as described
in [Evidence Package verification](evidence.md#independent-offline-verification), and
a trusted native APMX archive plus its checksum sidecar. Python is the
checker's runtime; the PyInstaller binary has its own embedded APMX runtime.
Use a candidate built from this checkout's committed source, including the
package-root shorthand and two-step packaged-factory consent flow. Older
published binaries do not necessarily support these commands.

Use the existing native release builder when creating a candidate:

```sh
python scripts/release.py build --target macos-arm64
```

Follow the APMX repository's `build/README.md` prerequisites, including a clean
committed source tree and matching selected build interpreter. A locally built
development candidate is not a published release. Keep notices and the whole
native bundle; do not copy just the executable.

From the source checkout, supply the trusted archive hash and checker interpreter:

```sh
python scripts/demo.py prepare "$HOME/Repos/apmx-demo" \
  --archive /absolute/path/to/apmx-0.4.2-macos-arm64.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256 \
  --python /absolute/path/to/checker-environment/bin/python \
  --verifier-python /absolute/path/to/independent-verifier/bin/python \
  --schemas /absolute/path/to/pinned-schema-cache
```

Use the archive/hash for your native platform. The helper checks the checksum,
uses the release owner's safe extraction and notice/provenance checks, and
refuses existing destinations or destinations inside Git. It copies the current
factory/seed with content inventories, retains the bundle, and installs the real
pinned capability separately into each consumer through bundled APM.
The helper creates a minimal consumer `apm.yml` declaring `../factory` when absent;
official APM resolves and locks its dependencies. Application source and the
original seed are not edited.

The checker and verifier environments must stay available. The kit copies and
inventories the maintained independent verification scripts; it does not clone
their logic into the demo helper or install their dependencies. Setup asks that
consumer to check its dependencies and pinned schema cache before creating the kit. Verification
setup is optional for running agents but required for `demo verify`.
No dependencies, credentials or
shell-profile changes are installed silently. Authenticate through `copilot`
and `opencode auth login` before the session; a version response is not login.
The kit is currently a POSIX terminal helper, not a Windows setup promise.
