# Install APMX

**Use the [v0.4.2 release page](https://github.com/danielmeppiel/apmx/releases/tag/v0.4.2)
after its native archives, checksums and manifest are listed.** Verify and
extract the archive using the [native installation steps](#install-a-prebuilt-archive)
below. Until those assets appear, use the
[pinned source route](#run-the-current-source-checkout). Do not substitute the
withheld v0.1/v0.2 archives. The immutable v0.4.0 and v0.4.1 tags record failed
release-preparation candidates; no GitHub release or native assets were
published for either tag.

The v0.4.2 native bundles include the APMX Python runtime and private **official
APM 0.30.0** backend. They need no APMX build, global APM installation or
experimental activation command. **Contract checks still need their own
tools**: this factory uses Python 3.12 and Behave 1.3.3.

## Choose release or current source

| Route | What you get |
| --- | --- |
| [Prebuilt v0.4.2](#install-a-prebuilt-archive) **(recommended when listed)** | Download, verify and extract a complete native bundle. No Python runtime installation for APMX itself. |
| [Pinned source](#run-the-current-source-checkout) | A working fallback or development route, including the [approved-client alternative](#managed-environment-installation-optional) for managed environments. |

Keep the current guide open while following either route. The example/source
pin below deliberately stays at the earlier proven revision
`be9c5be19f39284fa5f7b6416de6c37764184f2c`; it is not the new native release's
source identity. Its factory resources are compatible with the new native
runner. A version string alone does not identify a build.

**Result-version distinction:** v0.4.2 source and downloads return COMPLETE/0
after exact evidence finalization. Historical v0.3.2 downloads and the
`be9c5be` pinned fallback below still return UNPROVEN/21 for completed native
runs; those downloads and the historical demo are unchanged. See
[migration](results.md). To develop this behavior, use a reviewed 0.4.2
checkout, keep its own frozen lock and example files together, and use the
source environment/backend setup below without switching it to the historical
pin. Release availability is not a claim of fresh model execution.

## Install a prebuilt archive

The [v0.4.2 release page](https://github.com/danielmeppiel/apmx/releases/tag/v0.4.2)
is the publication location for the five platform archives below, their
checksum sidecars and the release manifest. Do not run the download blocks
until all three are listed. Published downloads do not require GitHub
authentication.

You need **Git** and the [native GitHub Copilot CLI](https://docs.github.com/en/copilot/get-started/cli-quickstart).
Authenticate through Copilot itself. APMX currently supports native Copilot
only; a Copilot account with model access is required, and model work can incur
usage charges. The APMX executable does **not** require a separately installed
Python, but Python is required for the example's checks described below.

Choose the archive matching your operating system and CPU:

| Machine | Archive |
| --- | --- |
| macOS, Apple Silicon | `apmx-0.4.2-macos-arm64.tar.gz` |
| macOS, Intel | `apmx-0.4.2-macos-x86_64.tar.gz` |
| Linux, x86-64 | `apmx-0.4.2-linux-x86_64.tar.gz` |
| Linux, ARM64 | `apmx-0.4.2-linux-arm64.tar.gz` |
| Windows, x86-64 | `apmx-0.4.2-windows-x86_64.zip` |

Each archive has a same-name `.sha256` sidecar. `release-manifest.json` binds
the five archives to the release version and full source commit.
Use a new installation directory, not an existing installation or evidence
directory. Stop on any failed download, checksum, extraction or version check.

### macOS and Linux native installation

The download commands use `curl` and do not need GitHub CLI authentication.
Change `target` to one of the four macOS/Linux targets in the table:

```sh
version=0.4.2
target=macos-arm64
archive="apmx-$version-$target.tar.gz"
install_dir="$HOME/.local/share/apmx/$version"
release_url="https://github.com/danielmeppiel/apmx/releases/download/v$version"
mkdir -p "$HOME/.local/share/apmx" &&
mkdir "$install_dir" &&
(
  cd "$install_dir" &&
  curl --fail --location --output "$archive" "$release_url/$archive" &&
  curl --fail --location --output "$archive.sha256" "$release_url/$archive.sha256"
)
```

Verify **before extracting**. On macOS:

```sh
(cd "$install_dir" && shasum -a 256 -c "$archive.sha256" && tar -xzf "$archive")
```

On Linux:

```sh
(cd "$install_dir" && sha256sum -c "$archive.sha256" && tar -xzf "$archive")
```

Only after verification and extraction succeed:

```sh
export APMX_NATIVE_ROOT="$install_dir/apmx-$target"
export PATH="$APMX_NATIVE_ROOT:$PATH"
command -v apmx
apmx --version
```

Expect the command under `APMX_NATIVE_ROOT` and version `0.4.2`. This PATH applies
to the current terminal. Keep the full extracted directory in place, then
continue with [example checker setup](#macos-and-linux-checker-setup).

### Windows native installation

Use PowerShell, **Git for Windows** with its `sh.exe` on PATH, and genuine native
**`copilot.exe`**, not an npm `.cmd` shim. Checks use `sh -c` even when APMX is
launched from PowerShell. Do not mix native Windows and WSL tools; inside WSL,
use the Linux archive and instructions instead.

```powershell
$version = "0.4.2"
$archive = "apmx-$version-windows-x86_64.zip"
$installDir = Join-Path $env:LOCALAPPDATA "apmx\$version"
$releaseUrl = "https://github.com/danielmeppiel/apmx/releases/download/v$version"
New-Item -ItemType Directory -Path $installDir -ErrorAction Stop | Out-Null
$archivePath = Join-Path $installDir $archive
Invoke-WebRequest "$releaseUrl/$archive" -OutFile $archivePath -ErrorAction Stop
Invoke-WebRequest "$releaseUrl/$archive.sha256" -OutFile "$archivePath.sha256" -ErrorAction Stop
$expected = (Get-Content "$archivePath.sha256" -Raw).Trim().Split()[0]
if ($expected -notmatch '^[0-9a-fA-F]{64}$') { throw "Invalid SHA-256 sidecar." }
$actual = (Get-FileHash $archivePath -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Checksum mismatch; do not extract this archive." }
Expand-Archive -LiteralPath $archivePath -DestinationPath $installDir -ErrorAction Stop
$env:APMX_NATIVE_ROOT = Join-Path $installDir "apmx-windows-x86_64"
$env:PATH = "$env:APMX_NATIVE_ROOT;$env:PATH"
(Get-Command apmx -ErrorAction Stop).Source
(Get-Command copilot -ErrorAction Stop).Source
(Get-Command sh -ErrorAction Stop).Source
apmx --version
if ($LASTEXITCODE -ne 0) { throw "APMX version check failed." }
```

Expect `apmx.exe` in the extracted directory, version `0.4.2`, native
`copilot.exe`, and `sh.exe` from Git for Windows. If `sh` is missing, add your
actual Git for Windows `bin` directory to this terminal's PATH. Continue with
[Windows checker setup](#windows-checker-setup).

### Keep the whole native bundle

Do not move just the executable. Keep its runtime, backend and notices together:

```text
apmx-<target>/
  apmx                    # apmx.exe on Windows
  _internal/
  LICENSE
  NOTICE
  LICENSES/
    manifest.json
  RELEASE.json
  apm-backend.json
  libexec/apm/
    apm                   # apm.exe on Windows
    _internal/
```

APMX uses that private backend, never a global APM from PATH. Do not run the
source route's `provision-apm` step for a native installation.
Checksums detect changed bytes; they are not publisher signatures. These
bundles are not publisher-signed or macOS-notarized. Do not disable OS security
checks to run them. The [source fallback](#run-the-current-source-checkout)
still provisions a native official APM backend: your platform must be compatible
with that backend, and your organization's policy must permit it. Source APMX
is not a workaround for Alpine/musl, older glibc compatibility or restrictions
on unsigned binaries.

To return in another terminal, add this same extracted directory to PATH and
reselect the checker environment described below. Keep the version, target and
release-manifest identity when reporting problems; do not infer that a historical
source run proves live execution on every native target.

## Example tools for a native installation

Use this section only after installing a verified factory-capable native APMX
archive. The executable includes its own Python runtime and APM backend, but
does not supply the tools used by a contract's independent checks.
The software-factory example needs **Python 3.12** and **Behave 1.3.3**.
Other contracts may use different tools.

The commands below clone the known example revision and create a **checks-only**
environment. They do not install APMX from source or replace the downloaded
executable. Do not run `uv sync` or install this checkout into the check
environment. Keep your organization's approved package proxy and certificate
configuration when installing Behave; do not disable certificate verification.

### macOS and Linux checker setup

With the native APMX directory already on PATH, use a new `apmx-examples`
directory and a native Python 3.12 installation with `venv` support:

```sh
git clone https://github.com/danielmeppiel/apmx.git apmx-examples &&
cd apmx-examples &&
git checkout --detach be9c5be19f39284fa5f7b6416de6c37764184f2c &&
test "$(git rev-parse HEAD)" = be9c5be19f39284fa5f7b6416de6c37764184f2c &&
python3.12 -m venv .apmx-checks &&
.apmx-checks/bin/python -m pip install 'behave==1.3.3'
```

Stop if setup fails. With some relocatable or uv-managed Python installations,
`python3.12 -m venv` can fail during `ensurepip` or standard-library discovery.
This is checker-environment setup, not evidence of a native APMX failure.
Preserve the failed environment and diagnostics. In a fresh pinned example
checkout with no `.apmx-checks` yet, the verified alternative is to replace
the two environment/dependency commands above with:

```sh
UV_PYTHON_DOWNLOADS=never uv venv --python 3.12 .apmx-checks &&
python3.12 -m pip --python .apmx-checks/bin/python install 'behave==1.3.3'
```

This requires an already installed Python 3.12 and a working, preconfigured
system pip client. `UV_PYTHON_DOWNLOADS=never` prevents a new interpreter
download. The approved system client installs only Behave and its dependencies
into the new environment; pip need not be installed inside it. Keep your approved
proxy and certificate settings. Do not use `uv sync` or install source APMX.

After either setup succeeds, select the checker environment:

```sh
export APMX_SOURCE="$PWD"
export APMX_CHECKS="$APMX_SOURCE/.apmx-checks"
export PATH="$APMX_CHECKS/bin:$PATH"
command -v apmx
if [ "$(command -v apmx)" = "$APMX_NATIVE_ROOT/apmx" ]; then
  python3 -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
else
  printf '%s\n' "Stop: restore the downloaded APMX directory on PATH." >&2
  false
fi
```

Expect `apmx` from the extracted native directory, Python from `.apmx-checks/bin/`
and Behave `1.3.3`. `APMX_SOURCE` identifies the example checkout here; it does
not mean APMX was installed from source. Keep both directories in place and this
PATH active. Continue with the [factory copy](../examples/contracts/software-factory/README.md#set-up).

### Windows checker setup

With native APMX on PATH and native Python 3.12 available through `py -3.12`,
use PowerShell:

```powershell
git clone https://github.com/danielmeppiel/apmx.git apmx-examples
if ($LASTEXITCODE -ne 0) { throw "Clone failed; stop here." }
Set-Location apmx-examples
git checkout --detach be9c5be19f39284fa5f7b6416de6c37764184f2c
if ($LASTEXITCODE -ne 0) { throw "Pinned checkout failed; stop here." }
$revision = git rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne "be9c5be19f39284fa5f7b6416de6c37764184f2c") {
  throw "Example revision mismatch; stop here."
}
py -3.12 -m venv .apmx-checks
if ($LASTEXITCODE -ne 0) { throw "Install native Python 3.12 with venv support first." }
& .\.apmx-checks\Scripts\python.exe -m pip install "behave==1.3.3"
if ($LASTEXITCODE -ne 0) { throw "Checker dependencies failed to install." }
$env:APMX_SOURCE = (Get-Location).Path
$env:APMX_CHECKS = Join-Path $env:APMX_SOURCE ".apmx-checks"
$env:PATH = "$(Join-Path $env:APMX_CHECKS 'Scripts');$env:PATH"
$apmxPath = (Get-Command apmx -ErrorAction Stop).Source
Write-Output $apmxPath
if ($apmxPath -ne (Join-Path $env:APMX_NATIVE_ROOT "apmx.exe")) {
  throw "Restore the downloaded APMX directory on PATH."
}
python -c 'import sys, behave; print(sys.executable); print("Behave", behave.__version__)'
if ($LASTEXITCODE -ne 0) { throw "Check Python/Behave setup before continuing." }
```

Expect native APMX from the extracted archive and Python from
`.apmx-checks\Scripts`. No activation script or execution-policy change is
needed. The `py` launcher creates the environment; subsequent checks use its
`python`, not a separate launcher environment without Behave. Continue with
the [Windows factory copy](../examples/contracts/software-factory/README.md#windows).

To repair checker dependencies later, reselect the existing checks-only
environment and run `python -m pip install 'behave==1.3.3'` through your approved
package configuration. Do not repeat the clone or install APMX into that
environment. If it was created by `uv venv` without pip, return to the example
checkout and use the approved system client instead:
`python3.12 -m pip --python .apmx-checks/bin/python install 'behave==1.3.3'`.

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

<a id="native-downloads-temporarily-withheld"></a>

## Legacy native archives remain withheld

The historical v0.1/v0.2 archives remain withheld and are not an installation
fallback. Their Linux bundles omitted the required libffi MIT notice. The new
v0.3.2 distribution passed the corrected third-party notice gates. The fresh
v0.4.2 distribution must independently pass the same gates; neither release's
availability makes those older archives acceptable.

For source history, v0.2.0 corresponds to
`2f0356d3e7ebb07f62911768198f9b0cd120cca9` and supports the older
single-contract runner, not this factory.

The historical hold concerns redistributed native bundles, not the APMX source license.
The source's Apache-2.0 terms and retained upstream MIT notices are unchanged.
Source installation still provisions the complete, checksum-verified
**official APM 0.30.0** backend from its upstream release; that acquisition is
separate from the withheld APMX archives.

## Migrating from v0.2.0 to v0.3.0

This section describes migration from older source or installations to v0.3.0.
These compatibility changes also apply to v0.3.2 and remain in v0.4.2. Use the
[prebuilt v0.4.2 release](#install-a-prebuilt-archive) or the pinned source
fallback; old native archives remain withheld.

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
Historical v0.3.2 downloads and the pinned source route retain scalar
`apm-contract-run/0.1` and multi-output `apm-contract-run/0.2` records. Current
v0.4.2 source and downloads use leaf schema `apm-contract-run/0.3` for both and
factory schema `apmx-contract-chain/0.2`. Consumers of records should inspect
the schema instead of assuming every delivery is one file. Existing runs,
snapshots and artifact paths are not migrated or rewritten.

Factories infer ordering from declared files; artifact tools edit private source
copies rather than overwriting caller source files. A complete local handoff needs
every declared output, passing
required checks, intact inventory and consent. For automation, pass both
`--allow-host-access` and `--allow-unproven-inputs`. Native execution remains
unsandboxed. Historical v0.3.2 and pinned runs remain **UNPROVEN / exit 21**,
even when checks pass; v0.4.2 source and downloads require complete validated
evidence for **COMPLETE / exit 0**. Behave remains an
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

For the smaller single-contract example, first complete either the Windows
native installation and checker setup, or the Windows source installation.
Keep the selected Python environment on PATH; a separate `py -3.12` launcher
is not used for these checks. Run:

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
**0 / COMPLETE** on v0.4.2 source and downloads, or **21** on the historical
v0.3.2/pinned routes. On v0.4.2, exit 21 is not success; on v0.3.2, inspect the
record's completeness and exact check/output evidence to distinguish successful
work from incomplete execution.
Inspect all required
outputs/checks and the printed record, not just the exit code.
