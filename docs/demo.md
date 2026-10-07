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
apmx --from ../factory . --on copilot --plan
```

Expect five stages, seven outputs and nine checks. Preview performs no model
calls. The application is the implicit workspace; source files need no inventory
in the contract. The factory supplies contracts, checks and the pinned testing
capability. The dot selects the whole factory package.

**"Let Copilot operate this factory."**

```sh
apmx --from ../factory . --on copilot
```

First confirm package preparation, then inspect the displayed factory and confirm
execution. Both prompts default to **No**. Preparation approval alone never
starts an agent. Native agents and checkers can
access host files, network and available logins; this is not a sandbox. Model
usage can cost money. Copilot keeps its configured model. The helper supplies
neither model overrides nor permission flags.

After successful execution and automatic evidence delivery, open the printed
Artifacts and Summary paths in your editor. Show the real code patch, separate
documentation patch and generated summary. Required checks assess their stated
scope; advisory review is not certification. The original application remains
unchanged. Never treat exit 21 or evidence-delivery exit 23 as a successful demo.

**"Change the harness, not the factory."**

Open a second terminal in the kit directory:

```sh
./demo opencode
```

Then run:

```sh
apmx --from ../factory . --on opencode --model github-copilot/gpt-5.6-sol
```

This is the explicitly tested OpenCode provider/model selection. It requires
your own model access; the helper does not change global OpenCode configuration.
Both applications start from the same seed and use the same factory.

Show the second run's code/docs outputs and evidence summary. Matching definition
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
and a trusted native APMX archive plus its checksum sidecar. Python is the
checker's runtime; the PyInstaller binary has its own embedded APMX runtime.
Use a candidate built from this checkout's committed source, including the
two-step packaged-factory consent flow; older published binaries require flags.

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
  --python /absolute/path/to/checker-environment/bin/python
```

Use the archive/hash for your native platform. The helper checks the checksum,
uses the release owner's safe extraction and notice/provenance checks, and
refuses existing destinations or destinations inside Git. It copies the current
factory/seed with content inventories, retains the bundle, and installs the real
pinned capability separately into each consumer through bundled APM.
The helper creates a minimal consumer `apm.yml` declaring `../factory` when absent;
official APM resolves and locks its dependencies. Application source and the
original seed are not edited.

The checker environment must stay available. No dependencies, credentials or
shell-profile changes are installed silently. Authenticate through `copilot`
and `opencode auth login` before the session; a version response is not login.
The kit is currently a POSIX terminal helper, not a Windows setup promise.
