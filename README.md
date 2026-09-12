# apmx

Run **one explicit contract** through native GitHub Copilot, retain its output,
and assess that exact output using independently captured checks.

This private standalone project does not require APM or experimental activation.
It is derived from Microsoft's MIT-licensed APM contract engine; see
[source origin and migration boundaries](docs/source-origin.md).

## Install a private release

Use an authenticated GitHub CLI account with access to this private repository.
Choose a published version from [Releases](https://github.com/danielmeppiel/apmx/releases).
For `v0.1.0`, once published, on Apple Silicon macOS:

```sh
version=0.1.0
target=macos-arm64
install_dir="$HOME/.local/share/apmx/$version"
archive="apmx-$version-$target.tar.gz"
mkdir -p "$install_dir" &&
gh release download "v$version" --repo danielmeppiel/apmx \
  --pattern "$archive" --pattern "$archive.sha256" --dir "$install_dir" &&
(cd "$install_dir" && shasum -a 256 -c "$archive.sha256" && tar -xzf "$archive")
export PATH="$install_dir/apmx-$target:$PATH"
apmx --version
```

Keep the **entire extracted folder**, including `_internal`; do not copy only
the executable. Available target names are `linux-x86_64`, `linux-arm64`,
`macos-x86_64`, `macos-arm64`, and `windows-x86_64` (ZIP rather than tar.gz).
Checksums detect changed download bytes; they are not publisher signatures.
These releases have **no publisher signing or macOS notarization**.

## Run

Install native GitHub Copilot CLI and Git. Authenticate with Copilot itself.
Install any tools explicitly required by the selected contract's checks.
Windows requires native `copilot.exe`, not an npm `.cmd` shim, and Git for
Windows' `sh.exe` for the same shell check language used on Linux/macOS.

```sh
# Start in this repository's source checkout; execute in a fresh external caller.
package="$PWD/examples/contracts/packaged-job"
caller="$(mktemp -d "${TMPDIR:-/tmp}/apmx-caller.XXXXXX")"
cp "$package/caller/notes.md" "$caller/notes.md" &&
cd "$caller" &&
apmx --from "$package" contracts/handoff.contract.md --on copilot --plan
apmx --from "$package" contracts/handoff.contract.md \
  --on copilot --allow-host-access
```

The example requires Python 3 for its independent checker. Alternatively, place
your trusted contract and required inputs in a fresh caller outside any Git
repository and run `apmx job.contract.md --on copilot --allow-host-access`.
Add `--model MODEL` to explicitly select a supported native model.
Do not remove remotes or policy configuration from an existing project to make
it eligible.

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
[The packaged-job example](examples/contracts/packaged-job/README.md) includes
a caller, package and imported skill.
On Windows use a native checker executable with POSIX-style quoting and
forward slashes, for example
`'"C:/Program Files/Python312/python.exe" -I checks/check_handoff.py'`.
Checks use `sh -c` on every platform, preserving compound commands and pipelines.
Git for Windows' shell is found on PATH or beside its Git installation.

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
