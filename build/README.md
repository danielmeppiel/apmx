# Standalone release engineering

`uv sync --frozen --extra dev --extra build` installs the publicly resolved lock.
`uv run --frozen --extra dev --extra build python scripts/release.py build --target macos-arm64`
creates a PyInstaller directory and `dist/assets/apmx-VERSION-macos-arm64.tar.gz`
with its SHA-256 sidecar. Use the runner-native target: `linux-x86_64`,
`linux-arm64`, `macos-x86_64`, `macos-arm64`, or `windows-x86_64`. The bundle
contains `apmx`/`apmx.exe`, `_internal`, `LICENSE`, `NOTICE`, `LICENSES`, and
`RELEASE.json`, `apm-backend.json`, and the complete official APM onedir under
`libexec/apm/`. Extract the whole archive and keep this layout intact: APM's
`_internal` stays under `libexec/apm`, separate from the application's runtime.

The app requires no APM installation or Python interpreter. Git, native Copilot
CLI, and tools explicitly named by a contract remain external prerequisites.
The build does not claim a trusted publisher signature or Apple notarization.
PyInstaller's macOS ad-hoc signature is not publisher identity. SHA-256 sidecars
are integrity checks, not signatures. Do not disable OS security checks.

`LICENSES` preserves the build interpreter's Python license, additional installed
runtime notices when available, and installed distribution license/NOTICE files.
Its manifest records the versions, copied files, license metadata, and explicitly
missing upstream notices. This intentionally includes the build-environment
distribution superset rather than claiming a complete bundled-dependency audit.
Missing Python license or a missing file declared by installed metadata blocks
the build. The original standalone/upstream MIT license and NOTICE remain at the
archive root.

## Pinned APM backend

`src/apmx/apm-backend.json` is the single version/source/asset authority. Builds
download the official native archive from `microsoft/apm`, verify its pinned
SHA-256 before extraction, reject unsafe paths/links/special files, require the
expected root, native executable and upstream license, and execute `--version`
to check the pinned version and source commit on the native runner. HTTPS
certificate verification remains enabled. All upstream resources and license
files remain unmodified inside the bundled onedir.

`RELEASE.json` records the backend version, full source commit, platform, original
archive name/hash, explicit bundled executable path/hash and pin-file hash.
Extraction verifies this provenance and byte-identical outer/runtime copies of
the pin. The outer archive hash anchors these files during fresh release-asset
verification; the pin and provenance are integrity evidence, not signatures.

For source development, provision a separate real backend explicitly:

```sh
python scripts/release.py provision-apm --target macos-arm64 --output dist/apm-backend
export APMX_APM_BACKEND="$PWD/dist/apm-backend/apm"
```

Use the matching native target and `apm.exe` on Windows. Provisioning requires a
fresh destination, never silently reuses host `apm`, and requires network only
to acquire the pinned backend. Released archives already contain APM and do not
download it at runtime. The development override is not consulted by frozen apmx.

## Acceptance boundary

`python scripts/smoke.py --binary /absolute/extracted/apmx --version 0.2.0`
uses a temporary caller outside the checkout, isolated HOME/config directories,
and no `PYTHONPATH` or `PYTHONHOME`. It does not import or install the application.
It rejects source launchers and runs six mandatory frozen local/package cases:
passing independent checks produce `UNPROVEN` (21), rejected output 20, and an
operational producer failure 22. It verifies retained source/output identities,
record completion, assessment bytes, transcript digest/size, process cleanup,
and unchanged caller and package input. Each package contains one self-contained
local skill; import, retained contract, and lock identities are checked. The
producer poisons its checker; the independent checker must
still use the baseline copy. Public commentary/final-answer deltas must appear;
private reasoning/tool sentinels must never appear in output or transcript.
Passing producers wait for a bounded acknowledgement from the smoke stdout
reader before completing, proving live delivery rather than only final capture.
The handshake is test-actor control outside caller/source/home, not an app hook.

Two additional frozen cases cover a quiet, result-only producer and a producer
that exits while leaving a heartbeat child. The latter must halt with
`lingering_children`, confirm cleanup, and leave the independently observed child
terminated with no continuing heartbeat. These **eight** cases run on all five
targets, including Windows; none is a help-only or skipped execution fallback.
Fixture children have their own finite lifetime and an emergency fixture-only
stop marker, which is written only after cleanup observations (or test failure).

Every gate checks the bundled APM executable hash and version/source against
release provenance. Before each of the original eight cases, the genuine bundled
backend installs a private package plus a transitive local skill, producing a real
lockfile and checked skill bytes. This also establishes APM's normal bootstrap
configuration before the unchanged-profile snapshot. All isolated HOME,
config/data/cache, Windows app-data and Copilot profiles must then remain
byte-identical. An executable `apm` refusal sentinel leads PATH, and the
source-only backend override points at that sentinel: frozen execution must
ignore both and records must identify the exact bundled backend.

A **ninth** package case starts from a genuinely empty profile. It allows only
official APM's documented bootstrap files, `~/.apm/config.json` and
`~/.cache/apm/last_version_check` on Unix or
`~/AppData/Local/apm/cache/last_version_check` on Windows, and rejects every other profile write or
plugin/hook/service activation. No production HOME rewriting, credential
copying, patched backend, or claim of zero APM host writes is introduced. All
ten cases run both on native CI archives and fresh downloaded release assets.
Only Copilot is simulated; APM's version and installs are genuine native execution.

A **tenth** mixed-ASF case imports the existing selected skill and an individually
named instruction from another dependency, not that entire package. The native
actor must receive both selected documents and the skill's byte-identical
reference, JSON asset and script-as-data resources. Their recorded path/hash/size
must match the original sources. An unselected sibling skill and hook fixture
must be absent from the prompt and staged contexts; the supporting script must
never execute. The original single-skill gates remain separate.

Short deadline/timeout behavior is covered separately by native-platform source
tests using the existing `ProcessRequest` API. The frozen gate does **not** claim
end-to-end default-duration timeout coverage. No public timeout flags, startup
hooks, source monkeypatching, or shortened frozen limits are introduced.

Copilot alone is an explicitly identified **hermetic JSONL protocol actor**,
not live model inference. The genuine independent fixture checker uses the
verification runner's Python interpreter, an external test prerequisite.
Independent checks retain `sh -c` semantics; Windows requires Git for Windows'
`sh.exe`. The shell and checker interpreter are not bundled in the app archive.
The interpreter's absolute path is quoted, and caller/package/tool paths include
spaces to exercise native argument handling.
On Windows, build a native actor with
`uv run --frozen --extra dev --extra build python scripts/smoke.py --build-actor dist/smoke-actor`
and pass `--actor /absolute/copilot.exe` to smoke. This does not test or authorize
shell interpolation of arbitrary prompts through a `.cmd` launcher. CI retains
the Windows fixture as separate, run-scoped test support, never as a release asset.
The actor is a PyInstaller **onedir** bundle: keep its `_internal` directory beside
`copilot.exe`, including when transferring it to a fresh verification runner.
One-file extraction is deliberately avoided because terminating a lingering
descendant also terminates its extraction-cleanup process, leaving fixture-only
temporary files. The strict app temporary-file cleanup check is not relaxed.
`--report PATH` retains a JSON report containing the validated fixture records.

## Private promotion

Ordinary CI builds and tests all five native targets. Release runs only for a
`vMAJOR.MINOR.PATCH` tag or an explicit manual request naming an existing tag.
The tag must match `pyproject.toml`, and jobs use its resolved commit throughout.
All Actions are official and SHA-pinned; dependency installation uses the frozen
public-index lock. Candidate, build/test, and workflow-default permissions remain
`contents: read`. Draft creation, downloaded-asset verification, and publication
jobs explicitly receive `contents: write`: GitHub's private draft APIs reject
read-only integration tokens even for inspection/download requests. Verification
uses `GH_TOKEN` only in the download step, never in frozen smoke execution, and
checkout does not persist credentials. No PAT, new secret, or global permission
change is required; exact asset/hash checks and publication gates are unchanged.

The release workflow reuses native CI, collects exactly five archives plus their
sidecars, and uploads them with a commit/version/hash manifest to a private draft.
Fresh native runners download the **actual draft release assets by asset ID**.
The manifest digest is anchored in the draft job's output, and the full candidate
asset identity fingerprint must remain unchanged. No app sources or app
installation are present in these verification checkouts. Each runner verifies
checksums, extracts with traversal/link/special-file guards, and repeats all ten
functional cases. Publication requires every downloaded-asset job to pass and
rechecks repository privacy, tag identity, draft identity, and asset fingerprint.

A failure leaves the release as a draft for inspection. Existing releases are
never overwritten or silently reused. To retry a failed draft, an operator must
inspect it and explicitly remove it before rerunning, or choose a new version/tag;
the workflow does not delete releases. Access remains limited to the private
`danielmeppiel/apmx` repository. Running this workflow is an explicit release
request; no AI credentials, corporate signing keys, or native auth profiles are
copied into CI. A separate operator-run downloaded-binary test against genuine
authenticated Copilot is required before claiming live model execution.

Infrastructure-only checks, before the source package exists:

```sh
python3 -m unittest discover -s tests/release -v
actionlint .github/workflows/ci.yml .github/workflows/release.yml
```

Those checks are not evidence of a functional frozen application build.
