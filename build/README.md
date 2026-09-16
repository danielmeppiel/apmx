# Standalone release engineering

**Native distribution is on hold pending third-party notice remediation.**
Historical v0.1/v0.2 Linux bundles omitted the required libffi MIT notice.
This page describes build mechanics, not approval to publish or redistribute
existing or newly built archives. Use the
[pinned source preview](../docs/install.md#run-the-current-source-checkout);
[native downloads are temporarily withheld](../docs/install.md#native-downloads-temporarily-withheld).

Select a reviewed build interpreter explicitly before using the commands below:
set `APMX_BUILD_PYTHON` and `UV_PYTHON` to the same absolute Python executable
(`python.exe` on Windows). The workflows bind both to the exact `setup-python`
output, verify the actual base executable, and record its version, build,
framework profile and executable SHA-256. `UV_PYTHON_DOWNLOADS=never` alone is
not interpreter selection. No interpreter replacement or OS installation is
performed by the native notice gate.

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
the build. The archive-root LICENSE covers APMX-specific additions under
Apache-2.0; NOTICE retains the original Microsoft APM MIT grant and attribution.

`LICENSES/native-manifest.json` separately inventories actual native-file
signatures and SHA-256 identities in both runtimes. Inventory traversal uses the
declared target's path-component ordering, not the verifier host's: Windows
ordering for Windows archives, POSIX ordering otherwise. Stored path spelling,
all record fields, multiplicity and whole-inventory equality remain exact;
verification never reorders or rewrites the stored manifest.

Every redistributed Linux
`libffi*.so*` file must independently match a reviewed Ubuntu Noble
`libffi8 3.4.6-1build1` library identity. Its exact upstream 3.4.6 LICENSE and full
Ubuntu source-package copyright file are copied under `LICENSES/native/libffi/`.
The source copyright file retains file-scoped holders and grants; its separate
build/test-tool licenses are not relabeled as the runtime library's MIT grant.

Unknown library bytes, missing/truncated/changed notices, inventory omissions,
and a changed inventory hash in `RELEASE.json` block archiving and extracted
bundle verification. Matching a reviewed package member establishes byte
identity, not original acquisition or an APT signature chain. A `_ctypes`
consumer with undefined `ffi_*` symbols is not assigned an embedded libffi
implementation or an invented version. Runtime provider resolution and full
system-loader closure are not claimed by the notice inventory.

Generic CPython licensing does not cover arbitrary statically embedded native
components. A private local preflight exposed this in an unsupported uv-managed
Python-build-standalone install-only runtime: its wrapper Python contained
defined libffi implementations, but its installed distribution supplied only a
generic Python LICENSE, not the complete native-license bundle. That archive
remains withheld; functional success does not clear redistribution.

For this release, macOS native builds require a selected framework CPython.
Supporting the incomplete install-only compiler is not required; do not borrow
the Linux libffi grant for an unidentified static implementation. Source
development and checker-only Python environments are not restricted by this
native-compiler policy. A portable Mach-O load-command/symbol-table check rejects
unmapped defined `ffi_*` implementations, including any universal-binary slice;
undefined system-library consumers are not mislabeled. Malformed evidence or
missing Python-core symbol evidence also refuses. The same guard runs during
collection, archive validation and downloaded extraction, independently of
manifest claims. It adds no platform-tool or runtime dependency.

Build-time interpreter selection and archive verification are separate:
`RELEASE.json` records the verified selected interpreter profile, while archive
checks validate that profile and actual native bytes **without inspecting the
verifier host's interpreter**. Linux draft staging can therefore validate macOS
archives. If a hosted provider supplies an unexpected unsupported runtime, the
gate refuses rather than presuming that `setup-python` proves notice coverage.

Builds require a clean committed tree. `RELEASE.json` binds `source_commit` and
the native-notice inventory digest; public draft creation rechecks all five
archives against the reviewed commit before upload. The original backend
runtime and upstream grants are not rewritten. Required notice source files
are hash-checked and exempted from Git line-ending conversion; only the exact
Ubuntu copyright file permits its upstream trailing whitespace.

## Pinned APM backend

`src/apmx/apm-backend.json` is the single version/source/asset authority. Builds
download the official native archive from `microsoft/apm`, verify its pinned
SHA-256 before extraction, reject unsafe paths/links/special files, require the
expected root, native executable and upstream license, and execute `--version`
to compare the exact platform-specific `version_output` in the pin. Official
Unix assets print the version and short source commit; the official Windows
asset prints only the version. Its source identity is anchored by the pinned
official archive/source provenance, not an invented printed commit. Missing
Unix commits, wrong versions, and arbitrary output prefixes/suffixes refuse. HTTPS
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

`python scripts/smoke.py --binary /absolute/extracted/apmx --version 0.3.1`
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
Windows also intentionally retains an empty `.apm_empty_gitconfig` in its
temporary directory. Only the fresh case may add that exact root-relative file
inside its owned temporary directory: it must be regular, non-symlink,
non-reparse, single-link, zero-byte, and have the empty SHA-256 digest. The
report names this third native bootstrap artifact. Nonempty content, aliases,
other paths, mutations of preexisting files, and every other temporary change
still refuse. No global or preexisting file is deleted to manufacture cleanup.

A **tenth** mixed-ASF case imports two logical packages, one containing the existing
skill and another containing an instruction and a differently named contained
skill. It never imports primitive symbols as independent package identities. The native
actor must receive all three selected documents and the first skill's byte-identical
reference, JSON asset and script-as-data resources. Their recorded path/hash/size
must match the original sources. An unselected dependency's skill and an unsupported hook fixture
must be absent from the prompt and the entire producer workspace; the supporting script must
never execute. The original single-skill gates remain separate.

An additional **factory** case previews a directory without creating execution
state or invoking the producer, then executes two dependent contracts. The first
delivers two files; the second consumes both and delivers a third. It checks
complete records, both explicit consent flags, scalar and multiple-output record
schemas, independent check and producer cleanup, immutable original inputs/checks,
the final artifact view, and **UNPROVEN (21)**. This reuses the existing hermetic
actor and checker; it is not live model inference. The ten local/package cases
remain unchanged in scope and run alongside this factory case before upload and
again against the actual downloaded draft bytes.
Its allowed generated writes are exactly descendants of `factory/.apm`, compared
as native path components. Windows separators are not mistaken for unexpected
writes, and literal POSIX backslashes are not reinterpreted as separators.

That same tenth case also generates a real consumer lock with native APM against
a genuine Git repository tagged `v9` with package version `9.0.0`. A hermetic SSH
transport serves actual Git objects; it does not replace APM, its resolver,
checkout, or lock writer. The packaged contract names the same Git dependency
with a deliberately nonexistent publisher ref. The run must still use the
consumer's exact commit, version, and context bytes, retain its original lock
byte-for-byte, preserve that entry in the effective native lock, and leave both
caller and package snapshots unchanged. This is package identity precedence,
not a same-basename local-directory approximation.
Only that actual frozen mixed-case invocation enables Git Trace2 in an owned
control file outside caller, package, profiles and temporary storage. An
unexpected exit includes bounded, redacted native Git error events, or
command-name/exit-code summaries when no error event is available. The initial
consumer seed does not receive this trace setting; Git configuration, SSH and
temporary-directory settings are unchanged by diagnostics.
The read-only Trace2 config filter is restricted to `core.longpaths`; only its
boolean value is reported, never authentication configuration values.

Every frozen case gets a separate compact, owned system-temporary directory for
`TMPDIR`/`TMP`/`TEMP`; its contents must be unchanged after execution, and the
unused case-local temporary directory must remain empty. This avoids introducing
Git-for-Windows path amplification through the fixture's own environment.
The consumer-lock case deliberately retains a long caller path. It generates
the initial manifest, native lock and modules in a compact owned stage, then
copies those exact bytes to the long caller without copying activation
directories or rewriting the lock. Absolute local anchors must remain portable.
The app itself must resolve in its own compact temporary stage outside that
caller and clean it afterward. On Windows, only the initial native consumer
setup subprocess gets an appended process-local `core.longpaths=true` Git
configuration entry, preserving all existing entries. That fixture setting must
not reach the tested apmx process: the application's own Windows backend-child
bridge must handle native Git paths. No global Git configuration, HOME
rewriting, backend patch, CI-only temp-root override, or test skip is used.

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
shell interpolation of arbitrary prompts through a `.cmd` launcher. The new
workflows build this fixture locally in each Windows job and never upload it.
The actor is a PyInstaller **onedir** bundle: keep its `_internal` directory beside
`copilot.exe`. Downloaded-byte verification builds its own ephemeral actor, rather
than transferring fixture binaries without accompanying notices between runners.
One-file extraction is deliberately avoided because terminating a lingering
descendant also terminates its extraction-cleanup process, leaving fixture-only
temporary files. The strict app temporary-file cleanup check is not relaxed.
`--report PATH` retains a JSON report containing the validated fixture records.

## Manual public experimental promotion

The legacy CI, tag-release and bootstrap workflow identities remain disabled.
Do not enable or rerun them to publish older branches or quarantined archives.
The replacement `native-notice-build.yml` is reusable/call-only;
`native-notice-release.yml` is manual-only. Neither starts five-platform builds
on pushes, pull requests or tags. Only `danielmeppiel/apmx` as a public, non-fork
repository with default branch `main` is allowed by explicit `--public-release`
policy. Calls without that flag retain the old private-only restriction.

The v0.3.0 preparation stopped before draft creation because the new factory
fixture compared native Windows paths with a POSIX string prefix. All four Unix
builds passed; their artifacts and the existing v0.3.0 source tag remain evidence,
not binaries to relabel. That fixture correction was included in v0.3.1.

All five v0.3.1 builds and native smoke matrices then passed, but Linux draft
staging stopped before release creation: host-dependent path sorting reordered
the otherwise identical Windows native inventory. The v0.3.2 correction uses
the declared target's path-component ordering while preserving exact inventory
comparison, field values and record multiplicity. Both earlier source tags and
their archive evidence stay unchanged; v0.3.2 requires fresh builds, not
repacked or relabeled earlier archives.

An operator first reviews and merges the candidate, authorizes creation of the
`v0.3.2` tag at that exact main commit, then explicitly dispatches
`native-notice-release.yml` on `main` with `phase=prepare` and `tag=v0.3.2`.
Tag creation, workflow dispatch and publication are separate human-controlled
actions, not consequences of opening or merging a source PR. Keep main at that
reviewed revision through preparation and publication: every checkout uses
the trusted workflow's `github.sha`, and the tag must equal it. The workflows
never execute arbitrarily selected historical source with current write tokens.

Preparation builds fresh archives on these standard public GitHub runners:

| Target | Runner |
| --- | --- |
| Linux x86_64 | `ubuntu-24.04` |
| Linux ARM64 | `ubuntu-24.04-arm` |
| macOS x86_64 | `macos-15-intel` |
| macOS ARM64 | `macos-15` |
| Windows x86_64 | `windows-2025` |

These are not larger/paid runners; actual startup availability must still be
observed. Archive-transfer artifacts are uncompressed and retained for one day;
JSON license/provenance/smoke evidence and the verification receipt for seven
days. There are no dependency caches or actor-binary uploads. All Actions are
official and SHA-pinned; dependencies come from the frozen public-index lock.
Candidate/build/default permissions are `contents: read`. Only draft creation,
draft download and explicit publication receive `contents: write`, because draft
release APIs require it. Verification receives `GH_TOKEN` only in the download
step, not when running native bytes; checkouts do not persist credentials.

**Approving preparation authorizes public Actions archive downloads.** Candidate
archive artifacts are exposed through this public repository's Actions runs
(GitHub sign-in may be required), even while the GitHub release remains a draft.
One-day retention does not make this private binary staging. Review the notice
gate and this exposure boundary before authorizing the preparation dispatch;
the later decision authorizes GitHub release publication, not first exposure.

Before upload, preparation independently extracts all five archives, checks
required native notices/inventories and source-commit metadata, and creates an
**experimental prerelease draft**, never a latest release. Exactly five archives,
their sidecars and a commit/version/hash manifest are uploaded. Fresh runners
then download the **actual draft release assets by numeric asset ID**, require
the original manifest digest and unchanged full asset identity fingerprint,
apply checksum/traversal/link/special-file gates, and repeat all ten original
functional cases plus the multi-output factory case. Verification checkouts
contain release helpers, not application source or an installed application.

Only successful downloaded-byte checks for every target produce the small
`verified-native-draft` artifact containing `verified-draft.json`. **Preparation
stops there; it cannot publish the GitHub release.** Review actual bundled-component
notice coverage and cold installer/download evidence before authorizing GitHub
release publication; Actions archives have already been exposed. No old archive
is relabeled or silently repacked, and the distribution hold remains until fresh
downloads actually work.

For a separately approved publication, dispatch the same workflow on the same
main revision with `phase=publish`, the same tag, and the preparation's
`verified_run_id`, `release_id` and `assets_fingerprint`. This phase **never
rebuilds**. It verifies the successful manual run, trusted source/workflow,
all five effective downloaded job results, unexpired numeric receipt artifact and its
GitHub ZIP SHA-256, and exact receipt identity/attempt. It then rechecks the tag,
draft and full asset fingerprint before publishing that same draft, preserving
`prerelease=true` and `make_latest=false`. An expired, stale, failed or changed
receipt/candidate refuses; there is no automatic bypass.

Partial failed-job retries use each expected verifier's latest execution across
all attempts through the receipt's bound run attempt. A receipt-only retry may
reuse earlier successful verifier executions from that same source/run, but a
later failed, skipped or incomplete verifier cannot fall back to an older pass.
Duplicate latest executions, foreign identities, incomplete job pagination and
a receipt from an older attempt refuse publication.

A failure leaves the draft for inspection. Existing releases are never
overwritten, silently reused or deleted by these helpers. Do not repeatedly
dispatch or replace assets to hide a failure; stop for an explicit operator
decision. No new PAT, AI credentials, corporate signing keys or native auth
profiles are copied into CI. Hermetic native smoke is not live-model proof.

Each native job provisions the real pinned backend before running source tests,
passing only its explicit path to that test step. The existing non-skippable
`tests/unit/test_wheel_isolation.py::test_wheel_runs_without_checkout_or_apm`
builds a genuine setuptools wheel and launches it from an extracted temporary
location with Python `-I -S`, without checkout/editable or `apm_cli` fallback.
It covers the console entrypoint, TLS resources, license/NOTICE files and
packaged pin JSON. This runs inside the existing all-five-platform unit step,
not a second matrix. A pure Python wheel is not claimed to contain native
backends; complete native backend distribution is proven by the frozen archives.

Infrastructure-only checks, before the source package exists:

```sh
python3 -m unittest discover -s tests/release -v
actionlint .github/workflows/ci.yml .github/workflows/release.yml
```

Those checks are not evidence of a functional frozen application build.
