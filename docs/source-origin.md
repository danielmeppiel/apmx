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

The `apm experimental enable contracts` prerequisite is removed. No APM package,
global configuration, experimental flags, activation, installers, plugin graph,
policy-fetch service, registry, or deployment ledger is required to run `apmx`.
Manifests are projected into a bounded read-only contract profile; lock dependency
rows retain their original codec, while unrelated deployment state is not emitted.
Offline no-policy admission remains fail-closed: callers with configured policy,
disabled policy discovery, or unresolved Git remote governance are refused.

Package acquisition is a direct, bounded Git operation using the retained
AuthResolver rather than the APM installer/downloader graph. Local directories,
HTTPS/SSH Git repositories, literal revisions, exact caller lock replay, and
same-repository imported skills are supported. Registry/proxy sources, insecure
HTTP and semantic-version ranges are refused explicitly. There is no automatic
model retry. Native Copilot login/profile ownership remains with Copilot; `apmx`
does not copy profiles or inspect native credential values.

## State and format compatibility

`apm.yml`, `apm.lock.yaml`, legacy `apm.lock`, `apm_modules` and package `.apm`
resources keep their original spellings. Renaming them would break input format
and resource discovery compatibility.

Runs deliberately remain under the **caller's** `.apm/runs/<fresh-run-id>/`.
This is local evidence compatibility, not access to global `~/.apm/config.json`.
The existing `apm-contract-run/0.1` record schema and `native-advisory` profile
remain readable by existing consumers. Temporary package preparation uses
exclusive `.apmx-source-*` directories and is removed after use. Run IDs prevent
overwriting prior evidence. Producer and checker workspaces are separate.

## Platform adapter

POSIX retains bounded original-process-group supervision, without claiming
containment of descendants that escape that group. Windows uses a separate
native process adapter and direct `.exe` arguments; `.cmd`/`.bat` prompt
interpolation is unsupported. On Windows, check strings use POSIX-style quoting
and forward-slash paths to a direct executable, not shell pipelines. Frozen
children restore external loader paths rather than inheriting bundled libraries.

No sandbox, cryptographic evidence signature, notarization, publisher signature,
or production/live-inference claim follows from a green fixture test.
