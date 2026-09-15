# Install APMX

APMX bundles its Python runtime and **official APM 0.30.0** in native release
archives. You do not need a separate APM installation or an experimental
activation command.

You do need **Git**, the [native GitHub Copilot CLI](https://docs.github.com/en/copilot/get-started/cli-quickstart),
and any tools named by a contract's checks. Authenticate with Copilot itself.
The examples use Python 3.12 or newer; development and native builds use Python 3.12.
Gherkin is not required by APMX. The software-factory example opts into Behave;
its [setup instructions](../examples/contracts/software-factory/README.md#set-up)
install the optional check tools.

This repository is currently private. Downloading releases or cloning the source
requires an authenticated GitHub account with repository access.

## Choose release or current source

| Route | What you get |
| --- | --- |
| [Published v0.2.0](https://github.com/danielmeppiel/apmx/releases/tag/v0.2.0) | A complete native bundle with APM and the single-contract runner. |
| [Current source checkout](#run-the-current-source-checkout) | Factory execution, multiple-artifact handoffs, native skill discovery and improved logs. These changes are not yet in v0.2.0. |

The published release was built from commit
`2f0356d3e7ebb07f62911768198f9b0cd120cca9`. This document does not imply a new
release has been published.

To obtain example files matching the published release, clone that tag:

```sh
gh repo clone danielmeppiel/apmx apmx-v0.2.0 -- --branch v0.2.0
```

That checkout includes the basic handoff examples, not the newer factory.
For development-branch examples, use the same source checkout as the
development runtime you install below; unpublished local commits cannot be
obtained by cloning the release tag.

## macOS and Linux release

Choose the target matching your machine:

| Machine | `target` |
| --- | --- |
| macOS, Apple Silicon | `macos-arm64` |
| macOS, Intel | `macos-x86_64` |
| Linux, x86-64 | `linux-x86_64` |
| Linux, ARM64 | `linux-arm64` |

With the [GitHub CLI](https://cli.github.com/) authenticated, run:

```sh
version=0.2.0
target=macos-arm64  # Change this using the table above.
install_dir="$HOME/.local/share/apmx/$version"
archive="apmx-$version-$target.tar.gz"

mkdir -p "$install_dir" &&
gh release download "v$version" --repo danielmeppiel/apmx \
  --pattern "$archive" --pattern "$archive.sha256" --dir "$install_dir"
```

Verify the download **before extracting it**. On macOS:

```sh
(cd "$install_dir" && shasum -a 256 -c "$archive.sha256" && tar -xzf "$archive")
```

On Linux:

```sh
(cd "$install_dir" && sha256sum -c "$archive.sha256" && tar -xzf "$archive")
```

After successful verification and extraction:

```sh
export PATH="$install_dir/apmx-$target:$PATH"
apmx --version
```

The PATH change applies to this terminal. Add the extracted directory to your
shell configuration if you want it available in future terminals. Repeating a
download into a populated directory may refuse existing files; use a fresh
directory rather than overwriting an installation in use.

## Windows release

Use native `copilot.exe`, not an npm `.cmd` shim. Independent checks use the same
`sh -c` command language on all platforms, so install **Git for Windows**, which
provides `sh.exe`.

In PowerShell, with GitHub CLI authenticated:

```powershell
$version = "0.2.0"
$archive = "apmx-$version-windows-x86_64.zip"
$installDir = Join-Path $env:LOCALAPPDATA "apmx\$version"
New-Item -ItemType Directory -Path $installDir -ErrorAction Stop | Out-Null
gh release download "v$version" --repo danielmeppiel/apmx `
  --pattern $archive --pattern "$archive.sha256" --dir $installDir
if ($LASTEXITCODE -ne 0) { throw "Download failed; do not extract this archive." }
$archivePath = Join-Path $installDir $archive
$expected = (Get-Content "$archivePath.sha256" -Raw).Trim().Split()[0]
$actual = (Get-FileHash $archivePath -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Checksum mismatch; do not extract this archive." }
Expand-Archive -LiteralPath $archivePath -DestinationPath $installDir -ErrorAction Stop
$env:PATH = "$(Join-Path $installDir 'apmx-windows-x86_64');$env:PATH"
apmx --version
```

The installation directory must be new. The PATH change applies to this
PowerShell session.

## Keep both runtimes together

Keep the **whole extracted folder**, not just `apmx` or `apmx.exe`:

```text
apmx-<target>/
  apmx                  # apmx.exe on Windows
  _internal/
  libexec/apm/
    apm                 # apm.exe on Windows
    _internal/
```

APMX selects its pinned private APM backend, never a host `apm` from PATH.
Checksums detect changed download bytes; they are not publisher signatures.
The current releases have **no publisher signing or macOS notarization**.

## Run the current source checkout

Use this route for the behavior and examples in the current development branch.
It requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and
Python 3.12. Start in the checkout containing this README; cloning the published
tag does not supply newer, unpublished changes.

```sh
uv sync --frozen --python 3.12
uv run --frozen python scripts/release.py provision-apm --target macos-arm64 --output dist/apm-backend
uv run --frozen apmx --help
```

Change `--target` to the target for your machine, including `windows-x86_64`
on Windows. Provisioning downloads and verifies the pinned official APM bundle;
it does not install a global APM command.

To use this source installation from an external caller directory, add its
entrypoint directory to the current terminal's PATH **while still at the
checkout root**:

```sh
export PATH="$PWD/.venv/bin:$PATH"
```

On Windows:

```powershell
$env:PATH = "$(Join-Path $PWD '.venv\Scripts');$env:PATH"
```

Confirm the source revision and which command your terminal will use:

```sh
git rev-parse HEAD
command -v apmx
```

The command should come from this checkout's `.venv/bin/`, not a previously
installed release. On PowerShell, use `git rev-parse HEAD` and
`(Get-Command apmx).Source`; expect this checkout's `.venv\Scripts\apmx.exe`.
A shared local native build needs its source provenance separately
(`LOCAL-BUILD.json`, when provided). `apmx --version` alone cannot distinguish
new development builds from the older release carrying the same version.

Source/wheel development may use `APMX_APM_BACKEND` with an absolute path to an
explicitly provisioned backend executable. Native frozen releases ignore this
override.

To build a complete native bundle instead:

```sh
uv sync --frozen --python 3.12 --extra build
uv run --frozen --extra build python scripts/release.py build --target macos-arm64
```

Use the target for your current machine; this is not cross-compilation.

## Migrating from v0.2.0 to v0.3.0

This section describes the v0.3.0 candidate. A version bump in source is not
publication: check `gh release list --repo danielmeppiel/apmx` for available
releases before downloading. The v0.2.0 download examples above remain usable.

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

Use a fresh disposable caller for the examples, outside a Git repository with a
remote or configured policy. Do not remove a real project's remotes or policy
to bypass admission.

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

With APMX on PATH, Copilot authenticated, and the native Python launcher
available as `py -3.12`, start at this checkout's root:

```powershell
py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 with its native py launcher first." }
$example = Join-Path $PWD "examples\contracts\first-contract"
$caller = Join-Path $env:TEMP ("apmx-first-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $caller -ErrorAction Stop | Out-Null
Copy-Item "$example\*" $caller -Recurse -ErrorAction Stop
$contract = Join-Path $caller "handoff.contract.md"
$text = [IO.File]::ReadAllText($contract).Replace("python3 checks/", "py -3.12 checks/")
[IO.File]::WriteAllText($contract, $text, [Text.UTF8Encoding]::new($false))
Set-Location $caller
apmx handoff.contract.md --on copilot --plan
if ($LASTEXITCODE -ne 0) { throw "Preview failed; inspect its explanation before running." }
apmx handoff.contract.md --on copilot --allow-host-access
```

Only the disposable contract copy is adjusted to use the Windows launcher.
The source checkout stays unchanged. A completed run with passing checks returns
**21**, not 0; inspect the printed output and record paths.
