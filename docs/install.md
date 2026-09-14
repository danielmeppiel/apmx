# Install APMX

APMX bundles its Python runtime and **official APM 0.30.0** in native release
archives. You do not need a separate APM installation or an experimental
activation command.

You do need **Git**, the [native GitHub Copilot CLI](https://docs.github.com/en/copilot/get-started/cli-quickstart),
and any tools named by a contract's checks. Authenticate with Copilot itself.
The examples use Python 3.12 or newer; development and native builds use Python 3.12.

This repository is currently private. Downloading releases or cloning the source
requires an authenticated GitHub account with repository access.

## Choose release or current source

| Route | What you get |
| --- | --- |
| [Published v0.2.0](https://github.com/danielmeppiel/apmx/releases/tag/v0.2.0) | A complete native bundle with APM and the single-contract runner. |
| [Current source checkout](#run-the-current-source-checkout) | This branch's newer native skill discovery, preparation logs, readable prose, and examples. These changes are not yet in v0.2.0. |

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

## Before running a contract

Run only contracts you trust. `--allow-host-access` permits Copilot, checks and
package preparation to use host files, network and available login details.
Native tool restrictions are not a sandbox.

Use a fresh disposable caller for the examples, outside a Git repository with a
remote or configured policy. Do not remove a real project's remotes or policy
to bypass admission.

On Windows, a trusted contract's check should name a native Python executable,
using forward slashes and POSIX shell quoting, for example:

```yaml
verify:
  handoff: '"C:/Program Files/Python312/python.exe" -I checks/check_handoff.py handoff.json notes.md'
```

The [first contract walkthrough](../README.md#run-your-first-contract) and
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
