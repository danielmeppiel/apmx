# Changelog

## [Unreleased]

## 0.5.0 - 2026-10-14

### Added

- Run a source factory without per-file workspace declarations. APMX captures
  the consumer project implicitly and runs a five-stage factory that delivers
  checked code and documentation patches.
- Opt in to bounded repair. A contract with an attempt budget retries with fixed
  inputs and checks under one shared deadline and retains every rejected
  attempt. Contracts without a budget remain single-attempt.
- Run the same source factory through the native OpenCode 1.2.24 profile as
  well as native Copilot; genuine Copilot and OpenCode rehearsals completed the
  same definition.
- Get a portable receipt for every COMPLETE run, with or without APM
  dependencies: CycloneDX 1.5 inventory (explicitly zero components when there
  are none), in-toto/SLSA provenance and Test Result evidence. Receipts live in
  `.apm/<runs|chains|controllers>/<id>/receipt/` (formerly `evidence/`).
  REJECTED and HALTED runs get no receipt.
- Verify a receipt with `apmx audit <receipt-dir>`: integrity (SHA-256 index),
  standards (in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5), factory,
  checks, outputs (`--outputs` re-hashes delivered files) and ingredients,
  delegated to the bundled APM via `apm install --frozen` and
  `apm audit --ci [--policy]`. Receipts are reported as unsigned. Exit 0 valid,
  1 invalid or policy failure, 2 usage, missing receipt or unavailable
  ingredients; `--offline` and `--format json` are supported. `audit` is now a
  reserved word: run a directory named audit as `./audit`.
- The receipt verifier lives in `apmx.audit` with bundled pinned schemas;
  `scripts/verify_evidence.py` remains a thin wrapper that runs without
  importing APMX. `in-toto-attestation`, `protobuf`, `jsonschema` and
  `referencing` are now main dependencies.

### Changed

- Bundle official APM 0.33.0 (source commit `18c4c43`) instead of 0.30.0. APM
  now verifies every immutable requirement (microsoft/apm#3061): a packaged
  contract whose publisher ref is unavailable or incompatible with a consumer
  pin fails preparation before any producer launches, instead of the consumer
  pin silently winning. `apmx audit` keeps its workarounds for
  microsoft/apm#3194 and microsoft/apm#3195, treats `manifest-missing` as a
  no-policy check when deciding whether `--policy` was enforced, and compares
  deployed file hashes by path.
- Default run output tells one compact story per contract: `needs -> produces`,
  one `attempt n/N` line with per-check marks, producer handoff lines,
  `not started: waits on` for blocked contracts, and a final COMPLETE/REJECTED
  block with Outputs, Receipt and `Next  apmx audit <receipt>`. Interactive
  color terminals update the running attempt in place; redirected, CI and
  `NO_COLOR` output stay append-only. Capture details, phases, narration, tool
  calls, check commands and checker output remain available with `--verbose`.
- Default output stays readable with real harnesses. A rejected check's
  excerpt condenses a JSON report line to its reason (or failed row IDs), cuts
  other lines at 200 characters and points to `--verbose` and `transcript.log`.
  Harness stderr lines longer than 200 characters are cut with
  `... (full line: --verbose)`. Routine native-debug-mirror notices appear only
  with `--verbose`; native log changes or read failures are shown once per run.
  Every full line stays in the retained transcript.
- The observed execution model reads `not reported by OpenCode` (or Copilot)
  instead of `unknown` when the harness stream has no model field. Model names
  in native debug logs are never promoted to evidence.
- `--plan` and the interactive consent preview show one compact work list
  (`[i/N] name   needs -> produces`, checks, attempt budget, `*` for inputs from
  an earlier contract) ending in "Nothing ran" and the run command. The
  local-execution disclosure and package-loading consent are shorter with the
  same semantics and default No; the policy detail moved to `--verbose`.
- A single `.contract.md` accepts `--allow-unproven-inputs` as a no-op instead
  of failing with exit 2; factory semantics are unchanged.
- Receipt export failure returns command exit 23 without changing a recorded
  COMPLETE outcome. Scripts must distinguish execution completion from receipt
  delivery.

### Fixed

- Ctrl+C during a POSIX agent run is a cooperative stop request. An interrupt
  inside `Popen.poll()` no longer leaves apmx blind to the agent's exit and
  records `cleanup_confirmed: false` after the full cleanup window; an
  interrupt at the supervision loop boundary no longer escapes without a
  producer record; and macOS EPERM for exiting or zombie group members no
  longer aborts supervision. Cleanup is still confirmed only once the group is
  gone.
- The five-stage software-factory example can repair real-model mistakes.
  Documentation has its own budget (2 attempts, 300 seconds) and may link only
  delivered files. A well-formed patch that touches files outside its check
  profile is now a retryable rejection (exit 1) instead of invalid input
  (exit 2); malformed or unsafe patches still stop the stage. Rejected attempts
  report the candidate they assessed, so their receipts export and audit.
- Native CI binds to the selected build interpreter and its frozen acceptance
  exercises automatic receipt delivery and explicit ambiguous-inventory refusal.

## 0.4.2 - 2026-09-18

- Make the Windows logger presentation tests assert dim styling on actual
  elapsed-time metadata instead of the platform-dependent trailing line
  position.
- Use path-safe labels for native, LF and CRLF output variants while preserving
  exact newline translation, transcript identity and finalization coverage.
- Publish v0.4.2 as the patch release candidate. No GitHub release or native
  assets were published for v0.4.1; its immutable public tag remains the source
  record of the failed release-preparation candidate, and no v0.4.1 asset is
  reused.

## 0.4.1 - 2026-09-18

- Make the native release smoke fixture follow the current Copilot event and
  presentation protocol: public progress, completion and tool observations are
  proven from the retained transcript without requiring successful leaf
  narration to appear before native completion.
- Make transcript presentation assertions portable across LF and CRLF output
  streams while preserving byte-identical retained transcript and finalization
  checks.
- Prepare v0.4.1 as the patch release candidate. Release preparation failed on
  Windows-only presentation test assumptions before publication, so no v0.4.1
  GitHub release or native assets exist. Its immutable public tag remains the
  source record of that failed candidate. No GitHub release or native assets
  were published for v0.4.0 either; its immutable public tag remains unchanged.

## 0.4.0 (2026-09-18)

- Separate operational COMPLETE (exit 0) from native assurance. Completion
  requires all declared outputs retained, every exact required check passed,
  observed cleanup and validated, durably finalized evidence. Incomplete work stays nonzero.
- Revalidate retained identities after preparation cleanup, match factory
  nodes to exact distinct child records, and withhold success headlines until
  the invocation's completion boundary.
- Introduce leaf record schema 0.3 and factory schema 0.2 with independent
  execution and assurance fields. Old/unknown records fail closed for new
  handoffs; historical evidence is never rewritten. COMPLETE does not pass
  strict VERIFIED-only admission.
- Present Contract / Checks / Evidence consistently in default and verbose
  modes, with PASS/FAIL labels, artifact/check counts, copyable paths and
  one pre-execution local-access/cost disclosure. Retain routine narration
  and checker details without hiding live progress or failure diagnostics.
- Prepare fresh v0.4.0 native bundles from this release source. Release
  preparation failed before publication, so no v0.4.0 GitHub release or native
  assets exist. Historical v0.3.2 downloads and the pinned demo remain unchanged
  with UNPROVEN/21 outcomes; no v0.3.2 binary, asset or manifest is reused.
- Put factory work before consent: list every declared artifact and planned
  check with literal counts and execution-matching contract names. Disclose
  local access, package installation and cost once, immediately before the
  default-No prompt. Preview evidence paths describe future storage, not an
  existing record; `--plan` remains free of execution disclosure and consent.
- Give the six software-factory hero checks outcome-oriented names so consent
  states what each command proves, while keeping commands and validation
  semantics unchanged.

See [result meanings and migration](docs/results.md).
