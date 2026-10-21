# A three-act demo

**Story:** "Make delivery free from $50. Here are the contracts and the checks.
Don't trust the agent; trust the proof." About 10 minutes:

1. **Run the factory** on a laptop with a native harness.
2. **Prove it** with `apmx audit`, then the same factory on another harness.
3. **Accept the PR by proof:** the factory runs in CI, signs its receipt, and
   opens a pull request whose only gate is that same audit.

The macOS/Linux kit below separates machine preparation from the presentation.
It uses a genuine native APMX bundle, its official APM backend and an unchanged
five-stage factory. It does not simulate an agent, grant consent, apply patches
automatically, or change global shell or login configuration. The public
examples are the factory package
[danielmeppiel/apmx-demo-factory](https://github.com/danielmeppiel/apmx-demo-factory)
(tag `v1.0.0`) and the receiving repository
[danielmeppiel/apmx-demo-shop](https://github.com/danielmeppiel/apmx-demo-shop).

## Present

### Act 1: Run the factory

From the prepared kit directory (see [Prepare once](#prepare-once-backstage)):

```sh
./demo copilot
```

The shell starts inside the application with the native `apmx` and checker
Python on PATH, and prints the factory reference to use. Show `request.md`,
`../factory/contracts/build.contract.md` (its `produces`, `verify` and
`budget` blocks) and one check under `../factory/checks/`.

```sh
apmx --from danielmeppiel/apmx-demo-factory#v1.0.0 --plan
apmx --from danielmeppiel/apmx-demo-factory#v1.0.0
```

A kit prepared from the local example uses `--from ../factory` instead.
`--plan` shows five stages, seven outputs and nine checks without model calls
or installation. The run asks twice, both defaulting to **No**: once to load
the factory and install its pinned capability, and once to execute. Native
agents and checks can use host files, network and logins; this is not a
sandbox, and model use can cost money.

Narrate one line per contract: attempt, check marks, then `-> handed to`. If a
repair happens (`attempt 2/3`), narrate the check output, the result, then the
retry; never promise one. The run ends with **Outputs / Receipt / Next**: the
code and documentation patches, and the receipt they are bound to. Add
`--verbose` to stream agent activity and checker output; inspect it before
projecting, because native metadata can be sensitive.

### Act 2: Prove it

Run the printed `Next` command:

```sh
apmx audit .apm/chains/<run-id>/receipt
```

Walk the rows: **Integrity** (every file hashed in `index.json`),
**Standards** (in-toto, SLSA v1, CycloneDX 1.5, schema-valid), **Factory**
(the definition digest of these contracts, checks and package commit),
**Checks**, **Ingredients** (APM reinstalls the pinned capability and runs
`apm audit`), **Outputs**, and **Identity**: `[!] unsigned`. A laptop receipt
proves content binding, not who ran it. Act 3 adds the signature. See
[audit](audit.md).

For a deeper look, `demo proof` prints the factory-definition SHA-256, the SLSA
binding, the output subjects and the ABOM. `demo verify` and
`demo verify --controls` check the same evidence with an independent consumer
that does not import APMX, and test corruptions on disposable copies. From the
kit root, use `./demo proof copilot` or `./demo verify copilot`. `--run ID`
selects a historical run; by default the helper selects the newest attempt in
the active application.

**Change the harness, not the factory.** In a second terminal:

```sh
./demo opencode
apmx --from danielmeppiel/apmx-demo-factory#v1.0.0 --on opencode --model github-copilot/gpt-5.6-sol
apmx audit .apm/chains/<run-id>/receipt
```

The definition digest matches the Copilot run: same contracts, checks,
capabilities and budget. The generated code may differ. For a tight talk, run
one harness live and label the other as a prepared run.

### Act 3: Accept the PR by proof

The receiving repository's `apm-policy.yml` pins the reviewed factory
definition digest (the one Act 2 printed), `require_signed: true`, and the
signer workflow `danielmeppiel/apmx/.github/workflows/run.yml`. Its only PR
gate is `apmx audit`. See [Accept factory PRs by
proof](../examples/accept-factory-prs/README.md).

The factory runs in CI through `run.yml@v0.6.0` with `factory:
"danielmeppiel/apmx-demo-factory#v1.0.0"` and `checks-requirements:`, as three
jobs:

- **produce:** the agent, with a read-only token; the only job that sees the
  model token.
- **check:** a fresh runner and no agent; `apmx check` reruns the checks on
  the output bytes and writes the receipt of record.
- **attest:** OIDC signs the receipt, with no candidate code present.

Show two pull requests' `audit` checks:

- **The genuine delivery:** Policy, Factory (on `factories.allow`), Outputs
  (the PR's change is exactly the delivered patches, applied to the base) and
  Identity (`signed by .../run.yml@...`) pass: `VALID (authenticated)`.
- **The same delivery plus one unrelated edit:** the signature still verifies,
  but Outputs fails (`PR changes <file>, which the receipt does not bind`):
  `INVALID`, exit 1.

"The signature is valid. What fails is the binding: this PR is more than the
factory delivered, so it cannot merge."

### Honest limits

Say at least one. Local receipts are unsigned. A signature says which workflow
vouched for a structurally valid receipt; it does not make the agent
trustworthy, and checks assess only their stated scope. Native execution is not
a sandbox. Every workflow that calls `run.yml` at the pinned ref can sign; the
policy pins the reusable workflow, not its caller. Never present exit 20, 21,
22 or 23 as success. With no network, `apmx audit --offline` still checks
Integrity, Standards, Factory, Checks and Outputs.

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
  --archive /absolute/path/to/apmx-0.6.0-macos-arm64.tar.gz \
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

### Use a versioned factory package

To show the same factory definition that a shop's CI runs, prepare against a
pinned remote package instead of the local example copy:

```sh
python scripts/demo.py prepare "$HOME/Repos/apmx-demo" \
  --archive /absolute/path/to/apmx-0.6.0-macos-arm64.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256 \
  --python /absolute/path/to/checker-environment/bin/python \
  --verifier-python /absolute/path/to/independent-verifier/bin/python \
  --schemas /absolute/path/to/pinned-schema-cache \
  --factory-ref danielmeppiel/apmx-demo-factory#v1.0.0
```

Why: a receiving repository's `apm-policy.yml` pins the definition digest of a
CI run of `factory: "owner/repo#ref"`. That definition records the package's
repository, resolved commit and content hash. A `../factory` run records a
local source instead, so its digest never matches. With `--factory-ref`, each
consumer `apm.yml` declares the reference, official APM locks its commit, and
presenters run `apmx --from danielmeppiel/apmx-demo-factory#v1.0.0` (the demo
shell prints the exact command; `--plan` works offline from the lock). Compare
the definition SHA-256 from `demo proof` with the digest the policy pins.

Only GitHub `OWNER/REPO#REF` references are accepted, and the `#REF` pin is
required; prefer an immutable tag or a full commit. The kit then contains no
local example copy: `factory/` holds a read-only copy of the bytes APM
installed for that reference, for your editor. Every reset compares each
freshly installed package with it and stops if the reference moved; prepare a
new kit to adopt another version.

The checker and verifier environments must stay available. The kit copies and
inventories the maintained independent verification scripts; it does not clone
their logic into the demo helper or install their dependencies. Setup asks that
consumer to check its dependencies and pinned schema cache before creating the kit. Verification
setup is optional for running agents but required for `demo verify`.
No dependencies, credentials or
shell-profile changes are installed silently. Authenticate through `copilot`
and `opencode auth login` before the session; a version response is not login.
The kit is currently a POSIX terminal helper, not a Windows setup promise.
