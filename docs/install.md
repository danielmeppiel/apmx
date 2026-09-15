# Install APMX

**Start from pinned source for the experimental factory walkthrough.**
Install APMX and its private **official APM 0.30.0** backend together, then use
the [factory guide](../examples/contracts/software-factory/README.md#set-up)
to preview, execute and inspect the example. No global APM installation or
experimental activation command is needed.

## Choose release or current source

| Route | What you get |
| --- | --- |
| [Pinned source below](#run-the-current-source-checkout) **(recommended)** | Factory execution, multiple-artifact handoffs, native skill discovery and current logs, with matching example files. |
| [Native archives](#native-downloads-temporarily-withheld) | **Temporarily withheld.** This source preview does not offer or endorse native binary downloads. |

The pinned source reports version 0.3.0; that is not a published release,
tag or native archive. Its full commit ID, not `apmx --version`, identifies
the runtime and examples used here. No v0.3.0 archive is required or claimed.
Keep this guide open: these onboarding instructions are newer than the pinned
checkout's README, but use its unchanged runtime, contracts and check resources.

## Run the current source checkout

### Prerequisites

Install **Git**, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and the [native GitHub Copilot CLI](https://docs.github.com/en/copilot/get-started/cli-quickstart).
Open Copilot itself and authenticate before live execution. APMX currently
supports **native Copilot only**. Model access and usage charges come from your
Copilot account; APMX does not supply credentials or a hard spending cap.

The commands select **Python 3.12**; uv can provision it if needed. They include
the optional `factory` extra, which supplies **Behave 1.3.3** for this example's
Gherkin checks. Gherkin is not required by APMX or by other contracts.
Installation downloads dependencies and a checksum-verified APM distribution.
Preview later is offline with respect to model work and package preparation;
installation is not.

In a managed environment, use your organization's approved package proxy and
certificate configuration before running these commands. Do not disable that
configuration or certificate verification to make installation succeed; keep
the source pin, frozen dependency versions and hashes intact. An index setting
alone may not redirect frozen artifact URLs; see the
[managed-environment alternative](#managed-environment-installation-optional)
if direct package downloads are not permitted.

Choose the target for the machine where you will run APMX:

| Machine | `target` |
| --- | --- |
| macOS, Apple Silicon | `macos-arm64` |
| macOS, Intel | `macos-x86_64` |
| Linux, x86-64 | `linux-x86_64` |
| Linux, ARM64 | `linux-arm64` |
| Windows, x86-64 | `windows-x86_64` |

These are provisioning targets, not a claim that live inference was tested on
each one. The [recorded factory observation](../examples/contracts/software-factory/README.md#observed-run)
used macOS ARM64. Final-candidate native CI and Windows/Linux live execution
are not established by that observation.

### macOS and Linux

Use a new `apmx-source` directory. Run each block in the same terminal, and stop
if a command fails. The immutable pin keeps the runtime and example bytes
together even as the default branch changes:

```sh
git clone https://github.com/danielmeppiel/apmx.git apmx-source &&
cd apmx-source &&
git checkout --detach be9c5be19f39284fa5f7b6416de6c37764184f2c &&
test "$(git rev-parse HEAD)" = be9c5be19f39284fa5f7b6416de6c37764184f2c &&
git rev-parse HEAD
```

The last command must print
`be9c5be19f39284fa5f7b6416de6c37764184f2c`. An unavailable revision or access
error is a stop, not a reason to substitute `main` or an older release tag.

```sh
target=macos-arm64  # Change this using the table above.
uv sync --frozen --python 3.12 --extra factory &&
uv run --frozen --extra factory python scripts/release.py provision-apm \
  --target "$target" --output dist/apm-backend
```

Keep `--extra factory` on both uv commands: a later sync/run without it can
remove Behave. Provisioning requires a new `dist/apm-backend` directory and
refuses an existing destination. Do not overwrite a backend in use; for a new
installation use a fresh source checkout.

While still at the checkout root, select its entrypoints for this terminal:

```sh
export APMX_SOURCE="$PWD"
export PATH="$APMX_SOURCE/.venv/bin:$PATH"
command -v apmx
python3 -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
apmx --help
```

Expect `apmx` and Python under this checkout's `.venv/bin/`, and Behave `1.3.3`.
Keep this PATH active when moving to the caller directory; do not run
`uv run` there, where it could select a different project. Continue with
[copying the factory](../examples/contracts/software-factory/README.md#set-up).

### Windows source installation

Use **Git for Windows**, including `sh.exe` on PATH, and genuine native
**`copilot.exe`**, not an npm `.cmd` shim. Checks run through `sh -c`, even
when you launch APMX from PowerShell. Do not mix WSL and Windows runtimes;
inside WSL follow the Linux route instead.

In PowerShell, with Git, uv and native Copilot already installed:

```powershell
git clone https://github.com/danielmeppiel/apmx.git apmx-source
if ($LASTEXITCODE -ne 0) { throw "Clone failed; stop here." }
Set-Location apmx-source
git checkout --detach be9c5be19f39284fa5f7b6416de6c37764184f2c
if ($LASTEXITCODE -ne 0) { throw "Pinned checkout failed; stop here." }
$revision = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne "be9c5be19f39284fa5f7b6416de6c37764184f2c") {
  throw "Source revision mismatch; stop here."
}
uv sync --frozen --python 3.12 --extra factory
if ($LASTEXITCODE -ne 0) { throw "Python environment setup failed." }
uv run --frozen --extra factory python scripts/release.py provision-apm --target windows-x86_64 --output dist/apm-backend
if ($LASTEXITCODE -ne 0) { throw "APM backend provisioning failed." }
$env:APMX_SOURCE = (Get-Location).Path
$env:PATH = "$(Join-Path $env:APMX_SOURCE '.venv\Scripts');$env:PATH"
(Get-Command apmx -ErrorAction Stop).Source
(Get-Command copilot -ErrorAction Stop).Source
(Get-Command sh -ErrorAction Stop).Source
python -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
if ($LASTEXITCODE -ne 0) { throw "Check Python/Behave setup before continuing." }
apmx --help
if ($LASTEXITCODE -ne 0) { throw "APMX entrypoint failed." }
```

Expect APMX and Python in the checkout's `.venv\Scripts`, Copilot resolving to
native `copilot.exe`, and `sh.exe` from Git for Windows. If `sh` is missing,
add your actual Git for Windows `bin` directory to this terminal's PATH before
continuing. The selected environment, not a separate `py` launcher environment,
must contain Behave. No PowerShell execution-policy change or activation script
is needed: the PATH above selects the environment. Continue with the
[Windows factory copy](../examples/contracts/software-factory/README.md#windows).

### Keep the source installation together

Keep the checkout, its `.venv/` and **all of `dist/apm-backend/`**, including
APM's `_internal/` runtime and upstream license. Do not copy just the `apmx`,
`apm` or `.exe` files elsewhere. The editable APMX entrypoint finds this
checkout's provisioned backend even from an external caller.

APMX never selects a global `apm` from PATH. Source/wheel development can
explicitly override the backend with an absolute `APMX_APM_BACKEND` path;
this walkthrough does not need that override. If your terminal already sets
it, resolve that intentional override before relying on this installation's
backend. Frozen native releases ignore it.

To reopen the source installation in another terminal, return to the same
checkout and repeat the PATH selection block for your platform. Preserve the
source SHA when reporting a problem; a version string alone is not provenance.

### Managed-environment installation (optional)

`uv.lock` records canonical artifact URLs as well as versions and hashes.
Consequently, `uv sync --frozen` can request those URLs directly even when a
different package index is configured. Merely setting an approved proxy/index
does not prove that uv uses it for every download. If your environment requires
an approved package service, do not bypass it, remove hashes, rewrite the lock,
disable TLS verification or repeatedly retry a prohibited direct download.

The following alternative was exercised on macOS ARM64 with the same pinned
source, a fresh environment/cache and an already configured approved pip client.
It is **not** evidence that the default direct-download sequence succeeded.
Keep package-client configuration and installation logs private; do not copy
proxy addresses, credentials or certificate configuration into this repository.

Use your organization's approved client for these steps, **instead of**
`uv sync`; runtime source and locked dependency versions/hashes stay the same:

1. In a fresh checkout at the exact source pin above, create a new Python 3.12
   `.venv`, for example with `uv venv --python 3.12 .venv`. Export the canonical
   factory requirements using
   `uv export --frozen --format requirements-txt --no-emit-project --no-dev --extra factory`.
   Save that output in a private requirements file outside the checkout.
   Hashes are included by default; do not add `--no-hashes` or index-emission flags.
2. Have the approved pip client target that `.venv` and install the exported
   requirements with `--require-hashes --no-deps`. Also supply a separate hashed
   build requirement for **`setuptools==84.0.0`**, using its wheel/sdist SHA-256
   values from this same pinned `uv.lock`. Do not resolve an unpinned build
   environment: `--frozen` for the application lock does not itself freeze
   isolated build requirements. This source's editable build succeeded with
   that locked setuptools; a separate wheel package was not needed.
3. Install the pinned checkout with the approved pip client targeting the same
   environment: `install --no-index --no-deps --no-build-isolation --editable .`.
   Run its `check` command against that environment. For a preconfigured system
   Python with pip, `python3 -m pip --python .venv/bin/python` selects the target
   on macOS/Linux; keep the client separate from the newly created environment.
4. Provision APM without letting uv synchronize the prepared environment again:

```sh
uv run --no-sync --frozen --extra factory python scripts/release.py provision-apm \
  --target macos-arm64 --output dist/apm-backend
```

Use your machine's target and keep the entire resulting backend distribution.
Provisioning still downloads and verifies the pinned official APM archive from
GitHub; package-proxy setup does not change that download or its checksum.
`--no-sync` prevents uv from pruning or resynchronizing the compliant environment
through the original lock URLs. Directly invoking that environment's Python
on `scripts/release.py` is equivalent. Repeat the platform's PATH selection and
APMX/Python/Behave checks above, then continue to the same factory copy and run
commands. For later dependency repairs, repeat the approved hash-verified
installation process, not an unqualified `uv sync`.

## Native downloads temporarily withheld

This experimental preview is **source-only**. APMX native binary distribution
is on hold pending third-party notice remediation: historical v0.1/v0.2 Linux
archives omitted the required libffi MIT notice. Those legacy archives are
not an offered or endorsed installation route, and this guide provides no
release or CI-binary download commands. Locally rebuilding a bundle does not
by itself establish that its third-party notices are complete.

For source history, v0.2.0 corresponds to
`2f0356d3e7ebb07f62911768198f9b0cd120cca9` and supports the older
single-contract runner, not this factory. The pinned source route above is
the installation path for this preview.

The hold concerns redistributed native bundles, not the APMX source license.
The source's Apache-2.0 terms and retained upstream MIT notices are unchanged.
Source installation still provisions the complete, checksum-verified
**official APM 0.30.0** backend from its upstream release; that acquisition is
separate from the withheld APMX archives.

## Migrating from v0.2.0 to v0.3.0

This section describes migration from older source or installations to the
pinned v0.3.0 source candidate. A version bump in source is not binary
publication; native downloads are temporarily withheld as explained above.

**Breaking compatibility change: imported skill metadata.** A skill accepted by
v0.2.0 can now fail admission unless its authored `SKILL.md` declares a nonempty
`name` and `description`. Names must contain 1-64 lowercase letters or digits,
optionally separated by single hyphens; leading, trailing and consecutive
hyphens are not allowed. For example:

```yaml
---
name: handoff-style
description: Write concise handoffs.
---
```

Update the authored source package, publish/select its updated revision through
the normal dependency workflow, and keep `apm.yml` and the generated lock
coherent. Do not patch generated `apm_modules` copies or edit lock hashes by
hand. Preview the consuming contract again before consenting to execution.

Selected context names must not collide case-insensitively. Context declaring
unsupported activation metadata is rejected, including hooks, MCP/LSP servers,
agent/model overrides, allowed tools or execution context. These are admission
limits, not an instruction to strip capabilities from a skill that needs them.
Only explicitly selected package skills are projected into native discovery.
Ambient project skill trees are not captured implicitly, and naming
`.agents/skills`, `.github/skills` or `.claude/skills` files in `needs` cannot
activate them; use package `imports` instead. This does not prohibit unrelated
project skills from existing in your checkout.

Scalar contracts and existing single-contract invocations remain supported.
Scalar run records retain `apm-contract-run/0.1`; multiple-output inventories
use `apm-contract-run/0.2`. Consumers of records should inspect the schema
instead of assuming every delivery is one file. Existing runs, snapshots and
artifact paths are not migrated or rewritten.

Factories infer ordering from declared files; artifact tools edit private source
copies rather than overwriting caller source files. A complete local handoff needs
every declared output, passing
required checks, intact inventory and consent. For automation, pass both
`--allow-host-access` and `--allow-unproven-inputs`. Native execution remains
unsandboxed and **UNPROVEN / exit 21**, even when checks pass. Behave remains an
optional checker prerequisite for the software-factory example, not an APMX
requirement.

## Before running

Run only factories and contracts you trust. Execution permits Copilot, checks
and package preparation to use host files, network and available login details.
Native tool restrictions are not a sandbox. A factory asks for confirmation in
an interactive terminal; single contracts require `--allow-host-access`.
The [factory guide](../examples/contracts/software-factory/README.md#automation)
documents explicit permissions for automation.

Use a fresh disposable caller for the examples, outside any Git repository.
Do not remove a real project's remotes or policy to bypass admission.

On Windows, a trusted contract's check should name a native Python executable,
using forward slashes and POSIX shell quoting, for example:

```yaml
verify:
  handoff: '"C:/Program Files/Python312/python.exe" -I checks/check_handoff.py handoff.json notes.md'
```

The [first contract walkthrough](../examples/contracts/first-contract/README.md) and
[packaged handoff](../examples/contracts/packaged-job/README.md) explain their
specific checker arguments.

### Windows first run

For the smaller single-contract example, first complete the Windows source
installation above. Keep its native Python environment on PATH; a separate
`py -3.12` launcher is not required. Run:

```powershell
python --version
if ($LASTEXITCODE -ne 0) { throw "Select the source Python environment first." }
$example = Join-Path $env:APMX_SOURCE "examples\contracts\first-contract"
$caller = Join-Path $env:TEMP ("apmx-first-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $caller -ErrorAction Stop | Out-Null
git -C $caller rev-parse --show-toplevel 2>$null
if ($LASTEXITCODE -eq 0) { throw "Choose a caller outside any Git repository." }
Copy-Item "$example\*" $caller -Recurse -ErrorAction Stop
$contract = Join-Path $caller "handoff.contract.md"
$text = [IO.File]::ReadAllText($contract).Replace("python3 checks/", "python checks/")
[IO.File]::WriteAllText($contract, $text, [Text.UTF8Encoding]::new($false))
Set-Location $caller
apmx handoff.contract.md --on copilot --plan
if ($LASTEXITCODE -ne 0) { throw "Preview failed; inspect its explanation before running." }
apmx handoff.contract.md --on copilot --allow-host-access
```

Only the disposable contract copy is adjusted to use the selected native Python.
The source checkout stays unchanged. A completed run with passing checks returns
**21**, not 0; that code also covers incomplete work. Inspect all required
outputs/checks and the printed record, not just the exit code.
