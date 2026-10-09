# Changelog

## [Unreleased]

### Added

- `apmx audit <receipt-dir>` verifies a receipt row by row: integrity
  (SHA-256 index), standards (in-toto Statement v1, SLSA Provenance v1,
  CycloneDX 1.5), factory, checks, outputs (`--outputs` re-hashes delivered
  files) and ingredients, delegated to the bundled APM via
  `apm install --frozen` + `apm audit --ci [--policy]`. It reports receipts as
  unsigned. Exit 0 valid, 1 invalid/policy failure, 2 usage, missing receipt or
  unavailable ingredients; `--offline` and `--format json` are supported.
  `audit` is now a reserved word; run a directory named audit as `./audit`.
- The receipt verifier moved into `apmx.audit` with bundled pinned schemas;
  `scripts/verify_evidence.py` is a thin wrapper that still runs without
  importing APMX. Its runtime dependencies (`in-toto-attestation` from PyPI,
  `protobuf`, `jsonschema`, `referencing`) are now main dependencies.
- Capture the consumer project implicitly and run a five-stage source factory
  that delivers checked code and documentation patches without per-file workspace declarations.
- Support opt-in bounded repair with fixed inputs/checks, a shared deadline and
  retained rejected-attempt history; contracts without a budget remain single-attempt.
- Export eligible completed runs automatically as portable CycloneDX,
  in-toto/SLSA and Test Result evidence, with independent verification tools.
- Run the same source factory through the native OpenCode 1.2.24 profile;
  genuine Copilot and OpenCode rehearsals completed the same definition.

### Changed

- Every COMPLETE run now delivers a receipt (`.apm/<runs|chains|controllers>/<id>/receipt/`,
  formerly `evidence/`), with or without APM dependencies. Without dependencies the
  CycloneDX 1.5 inventory explicitly lists zero components. REJECTED/HALTED runs get no receipt.
- Default run output tells one compact story per contract: `needs -> produces`,
  one `attempt n/N` line with per-check marks, producer-side handoff lines,
  `not started: waits on` for blocked contracts, and a final COMPLETE/REJECTED
  block with Outputs, Receipt and Next. Interactive color terminals update the
  running attempt in place; redirected, CI and `NO_COLOR` output stay
  append-only. Capture details, phase lines, narration, tool calls, check
  commands and checker output moved behind `--verbose`, which keeps them all.
- Evidence-delivery failure returns command exit 23 without changing a recorded
  COMPLETE outcome. Scripts must distinguish execution completion from package delivery.

### Fixed

- Bind native CI to the selected build interpreter and exercise automatic
  evidence delivery and explicit ambiguous-inventory refusal in frozen acceptance.

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
