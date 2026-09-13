# Source origin and standalone migration

This project derives from the MIT-licensed Microsoft APM implementation at
commit `0bad35f56a66a736d44975834d4ad4faafe72795`, canonical branch
`danielmeppiel-apmx-entrypoint-plan`. The original repository and branch are
read-only migration sources. Their full history is preserved separately in the
private `danielmeppiel/apm-apmx-backup` repository and immutable
`apmx-canonical-2026-09-12` pin.

Microsoft's copyright and MIT terms remain in `LICENSE` and `NOTICE`. Changes
here are an independent standalone extraction, not an official Microsoft release.

## Extracted owners

- Contract frontend, model, stream, workspace, events, record, process and engine
  modules retain the captured-baseline and native-advisory semantics.
- The contract-specific human renderer retains public live narration, bounded
  transcripts, source attribution, spacing and hanging indents. Its engine label
  changes from APM to `apmx`; Copilot and independent checks retain their labels.
- Dependency reference/identity/selection, lock dependency rows, content hashing,
  bounded YAML, filesystem guards, AuthResolver and supporting credential/TLS
  helpers retain their canonical owners.
- Copilot's contract request and merged MCP inventory are extracted without its
  unrelated interactive prompt adapter or runtime installer.
- Relevant canonical parser, import, package, engine, stream, reliability and
  terminal regressions are adapted to this namespace. A test import guard rejects
  any attempt to import an installed `apm_cli`.

## Deliberate standalone seams

The `apm experimental enable contracts` prerequisite is removed. Starting with
0.2.0, release archives bundle the official APM 0.30.0 native distribution,
source commit `8c2e0d9c352e2ed0e8c56b40063a63e1dd4a1937`, beneath
`libexec/apm/` with its own intact `_internal`. The outer executable never looks
for APM on PATH. No separate APM installation or experimental activation is
required. Manifests and public lock inventory retain their APM formats.
Offline no-policy admission remains fail-closed: callers with configured policy,
disabled policy discovery, or unresolved Git remote governance are refused.

Official APM now owns package acquisition, transitive resolution, frozen lock
replay and authentication/provider policy. The former copied Git downloader and
single-dependency resolver have been removed. The apmx adapter runs APM from an
owned manifest snapshot with `--root` pointing to that same owned directory,
`--only apm --target agent-skills --no-trust-bin`, and child-only
`APM_NO_SCRIPTS=1`. Relative local declarations and corresponding lock coordinates
are anchored before native replay; original caller/package files remain unchanged.
Staging uses a compact, private system-temporary directory rather than extending
the caller's path: native APM's transactional Git paths can exceed Windows Git
limits under a deep checkout. The temporary parent must be outside the caller and
source; all staged files are removed through the existing guarded cleanup path.
The fresh temporary root itself is the source/deploy directory, without another
staging layer: Git for Windows also bounds explicit Git-directory paths separately
from its filesystem long-path support.
On Windows, only the APM child receives a process-scoped `core.longpaths=true`
Git setting, matching the pinned backend's own Git-cache convention. Existing
indexed Git configuration and authentication entries are preserved; malformed
configuration is refused, not reset. No global Git configuration is changed.
The separate local baseline Git adapter uses the same Windows-only filesystem
option on its command line while still stripping inherited Git overrides and
disabling hooks, filesystem monitors, and signing.
When adding a not-yet-declared contract, its declaration is appended to that
owned consumer manifest before full native resolution. Existing pins are retained
and compared after installation; the publisher's graph is never first resolved
as an independent consumer. This also lets APM choose a locked consumer version
when the publisher's requested version is unavailable.
There is no copied credential policy, HOME rewriting, profile copying, installer
access from the producer, or automatic model retry.

Unmodified APM may initialize `~/.apm/config.json` and its normal version-check
cache. This is native host access, not a zero-host-write or sandbox guarantee.
MCP/LSP integration, hooks, commands, agents, native plugin registration, bin
deployment and lifecycle scripts are not enabled by this invocation. The native
backend is supervised with the existing process cleanup/watchdog boundary;
native output is framed, best-effort redacted and escaped before display or
retention. No unfiltered backend stream is spooled to disk.

Dependency preparation has its own durable, apmx-owned APM lifecycle messages,
separate from `Preparing files for Copilot` and `Running Copilot`. For example:

```text
  [>] Installing packages with APM 0.30.0
  Temporary workspace; your project files are unchanged.
  APM > [>] Resolving ./skills/handoff-style...
  [+] Packages ready.
  [i] Imported handoff-style
```

The APM version is shown only after the backend passes the exact version check.
`--verbose` requests APM's own verbose diagnostics and also explains the operation
and options before installation:

```text
  Running: apm install (in a temporary workspace)
  APM options: --only apm --target agent-skills --no-trust-bin --verbose
  APM > Resolved dependency tree: 1 direct + 1 transitive deps (max depth 2)
  Skill: handoff-style (SKILL.md)
```

`Running` names the operation, not a copyable command with invented arguments.
The executable is the validated bundled/provisioned APM, not a PATH lookup;
the request and `--root` are supplied to the actual temporary install. A separate
consumer install is labeled `Installing project imports`; `Using locked versions`
and the verbose `--frozen` option appear only when that invocation replays a
consumer lock. Each actual install has its own scoped start/completion; logging
does not install again. `Packages ready` requires observed backend success and
unchanged backend identity. It does not claim that later context validation,
Copilot execution or checks have passed. `Using` lists only the packages selected
after frontend validation and workspace inspection, not the entire installed graph.

Real APM stdout and stderr lines stream live with `APM >` / `APM stderr >`
attribution. Default output hides recognized setup, performance and inactive-target
details, not unknown messages or warnings/errors. Verbose output shows all eligible
bounded lines, including APM's own verbose diagnostics. Native package counts and
timings remain attributed native observations, never fabricated apmx facts or
proof of a contract outcome. Default output abbreviates known source paths relative
to the caller and labels the temporary root; verbose output and the transcript
retain the full sanitized native lines.

Both pipes use the existing bounded line framer: partial credentials are withheld
until a complete line or EOF, oversized lines are omitted whole with one warning,
and subsequent output continues to drain. A single logger applies the existing
redaction and ASCII/control escaping, then retains the bounded beginning/tail in
`transcript.log` if a run is later admitted. Transcript omission counts remain
available in the run record. Preparation failure before admission creates no run
record and launches no producer. Backend-child-only color/progress settings prevent
inherited forced color from producing ANSI noise; the wrapper's own terminal
colors and spinner remain unchanged. A wide child output width avoids native
soft-wrapping paths or credentials before framing. Injected controls are still
escaped, not silently stripped.

APM lifecycle lines remain visible after the spinner stops and in captured text.
One blank line separates APM/import preparation from the `Job:` block; jobs
without APM preparation do not gain a leading blank line.
Backend diagnostics can contain sensitive data: redaction is best-effort, not
protection against every unknown secret. Review logs before sharing them. No
environment dump, raw stream spool or full argv is logged. Copilot protocol
housekeeping names stay in the private bounded transcript rather than obscuring
prose on the verbose screen; public/private message filtering and completion
checks are unchanged. Offline `--plan` never runs or reports an install; local
execution without imports does not claim APM ran.

Imports name APM packages, not arbitrary skill symbols or repository basenames.
The consumer's manifest and lock govern even a packaged contract. Without a
consumer environment, execution can resolve an ephemeral one; a package-owned
lock is provenance, not a competing consumer lock. Read-only planning never
installs, fetches, repairs or migrates missing dependencies.

APM 0.30 adds cache-pin metadata after recording remote package hashes. For
overlapping virtual packages, a child's `.apm-pin` can consequently change its
parent's installed tree. Verification first requires the ordinary canonical
package hash. Only on a mismatch may it omit exact, schema-and-commit-validated
markers at nested remote package roots present in the native lock inventory;
each descendant package is independently hash-verified. The resulting preimage
must match the **original native expected hash**. No installed bytes or locks
are rewritten, and unlisted markers or changed package content remain failures.
Records expose any omitted metadata paths as `managed_metadata`; observed source
hashes still describe the actual installed tree.

Selected skills enter `.agents/skills/<skill-name>/` in the actual Copilot
producer workspace, with their original `SKILL.md` bytes and supporting files.
Copilot discovers and loads them natively; their bodies are not embedded in the
contract prompt. Instruction-type imports remain passive context beneath
`_apmx_context/import-N/`. The destination/name authority is
`contracts/context_layout.py`; package aliases are not native skill names.
Supported sources are global `.apm/instructions/**/*.instructions.md`, root
`SKILL.md`, and collections in `.apm/skills/` or `skills/`. Selected skill
`references/`, `assets/`, and `scripts/` companions are bounded data; scripts are
never executed by the importer. Scoped instruction activation and context
metadata requiring additional tools, models, agents, hooks, forked contexts or
services refuse. Native skill names and descriptions are required; names must be
portable lowercase Agent Skills identifiers. Case-colliding context names refuse.
Tracked caller skill trees are not copied into the producer; inputs and outputs
cannot occupy those activation paths. Unselected package content is not exposed.
Personal and plugin skills remain governed by Copilot's host configuration, not
an apmx sandbox or isolated profile.

Native custom instructions remain disabled. Runs with imported skills expose
the native `skill` tool alongside `view` and `apply_patch`; other runs retain the
two-tool profile. Shell/network denial and the exact output write grant remain.
Use a skill name (the documented explicit form is `/handoff-style`) or a request
matching its description; no instruction to open a skill path is needed.
See [GitHub's native CLI skill documentation](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

`Imported handoff-style` reports preparation only. `Copilot > Loaded skill:
handoff-style` reports a successful, correlated native skill-tool call or a
native `skill.invoked` event. Both normal and verbose output show this receipt,
and the bounded transcript retains it. Skill bodies, tool results and other
arguments are not printed. Repeated observations of the same loaded skill are
deduplicated; a load receipt is not an output assessment.

The process supervisor allows natural descendant shutdown for the first
two-thirds of the existing cleanup budget (four of the default six seconds).
The remaining budget handles forced cleanup; it is not additional time.
This avoids premature halts for native clients that close after their leader.
Timeout/cancellation still request immediate termination, genuinely lingering
processes still halt, and success still requires confirmed group/job cleanup.

## State and format compatibility

`apm.yml`, `apm.lock.yaml`, legacy `apm.lock`, `apm_modules` and package `.apm`
resources keep their original spellings. Renaming them would break input format
and resource discovery compatibility.

Runs deliberately remain under the **caller's** `.apm/runs/<fresh-run-id>/`.
This is local evidence compatibility, distinct from APM's normal user configuration.
The existing `apm-contract-run/0.1` record schema and `native-advisory` profile
remain readable by existing consumers. Temporary package preparation uses
exclusive `apmx-*` system-temporary directories and is removed after use. Run IDs prevent
overwriting prior evidence. Producer and checker workspaces are separate.
Additive record fields include actual backend identity, consumer and effective
lock digests, package identities/versions, resolved references and commits,
verified package hashes where available, and exact selected document/resource
digests. A local manifest version remains self-declared, not a verified release.
Runtime version output is compared with the exact pinned platform output.
The official Windows binary does not print a source commit; its source identity
comes from the pinned official release/archive, not a fabricated reported SHA.

## Platform adapter

POSIX retains bounded original-process-group supervision, without claiming
containment of descendants that escape that group. Windows uses a separate
native process adapter and direct `.exe` arguments; `.cmd`/`.bat` prompt
interpolation is unsupported. Checks preserve `sh -c` semantics on all platforms;
Windows requires Git for Windows' native `sh.exe`. Use POSIX-style quoting
and forward-slash paths for Windows checker executables. Frozen
children restore external loader paths rather than inheriting bundled libraries.
Restoration happens once at the spawning boundary, preserving the user's original
library paths through repeated credential/environment preparation. The workspace
Git adapter's empty configuration file is invocation-private. Separately, the
bundled APM backend may bootstrap an empty `.apm_empty_gitconfig` in its Windows
temporary directory; that is native APM configuration state, not producer output.
Terminal rendering preserves copyable Windows backslashes
while continuing to visibly escape control characters and Unicode.

Temporary package cleanup preserves the existing owned-path fence and bounded
file-lock retries. Exhausted permission or removal failures propagate rather
than reporting successful cleanup; explicit internal `ignore_errors=True`
remains opt-in. Read-only retries refuse observed symlink/reparse components
before changing permissions. Failed prepared-source cleanup reports HALTED with
`source_cleanup`, including when invocation already produced a retained record.

No sandbox, cryptographic evidence signature, notarization, publisher signature,
or production/live-inference claim follows from a green fixture test.
