# Changelog

## [Unreleased]

## 0.7.1 - 2026-10-22

A PR cannot rewrite its own gate; green means accepted by proof, and nothing
else.

- **Gate on `pull_request_target`.** On `pull_request` a PR could edit or
  delete its own `audit.yml` and turn the required check green. The shipped
  templates now run the base branch's copy and execute nothing from the PR
  (#44). Update your `audit.yml`.
- **Not a factory delivery.** A PR without a factory attestation fails with a
  message that says how to merge it instead (#44).

### Security

- **A PR can no longer rewrite its own gate.** The shipped gate workflows
  (`examples/accept-factory-prs/audit.yml`, `examples/factory-platform/audit.yml`)
  ran on `pull_request`, which runs the PR's own copy: a PR could edit it to
  `exit 0` or delete it, and the required `audit` check turned green
  (reproduced on apmx-demo-shop). They now run on `pull_request_target` (the
  base branch's copy) with read-only permissions, check the PR head out
  without credentials and execute nothing but `apmx audit`. Update your
  `audit.yml` the same way.
- In a pull request, `apmx audit` no longer reads the PR's working tree for
  policy: with no `apm-policy.yml` at the base, a policy the PR adds was used;
  now org discovery applies. APM's policy discovery runs in an empty project
  that shares only the `origin` remote, and a `--policy` file inside the
  checkout is refused (exit 2).

### Changed

- A PR none of whose changed files carries a factory attestation now fails
  with `[x] Not a factory delivery: no changed file carries an attestation
  from factories.signers. This check only passes factory deliveries; merge
  other PRs through your normal review process (e.g. a ruleset bypass for
  maintainers).` (still exit 1; JSON `facts.delivery`). A failed lookup is
  INCOMPLETE (exit 2). Green means accepted by proof only.

## 0.7.0 - 2026-10-22

Receipts are one standard in-toto bundle; PRs carry only code; run factories
centrally across repos.

- **One receipt, any verifier.** A run's evidence is one
  `receipt.intoto.jsonl` (SLSA provenance, in-toto test results, CycloneDX)
  plus content-addressed `evidence/`, readable by stock in-toto and SLSA
  tooling (#41).
- **Review code, not paperwork.** Factory PRs carry only the delivered files;
  `apmx audit` finds their signed verification summary by file digest (#41).
- **One platform, many repositories.** A platform repository runs factories
  against other repositories through `run.yml` and a GitHub App, and delivers
  a pull request, a branch or a bundle (#40, `examples/factory-platform`).
- **A signer pin nobody can move.** `factories.signers[].workflow_sha` pins the
  reviewed `run.yml` commit; a commit or the annotated tag object both match
  (#38).
- Stages record their harness, its reported version and the requested and
  observed models. They are recorded only: no policy enforces them yet (#39).

**Breaking:** v1 receipt folders (`receipt/`) are no longer read, and there is
no compatibility reader or migration. apmx has no users of the v1 format.

### Changed

- **Receipt format (breaking).** Every COMPLETE run now writes, next to its
  record, `receipt.intoto.jsonl` (one unsigned DSSE envelope per line: a SLSA
  Provenance v1, one in-toto Test Result v0.1 per check of the final
  candidate, and the CycloneDX 1.5 inventory inline), `outputs/` (the delivered
  files) and `evidence/sha256/<hex>` (content-addressed definition, inputs,
  stage outputs, transcripts, rejected attempts and raw check reports). It
  replaces the `receipt/` folder (`definition.json`, `index.json`,
  `summary.md`, `abom.cdx.json`, `capability-bindings.json`, `provenance/`,
  `checks/`, `attempts/`, `candidates/`). On the five-stage example: 232 files
  (1.3 MB) become 1 bundle (37 KB) plus 31 evidence blobs (188 KB).
- Every statement names the same subjects: the delivered files at their
  project paths. For patch outputs those are the files the patches produce
  (`src/pricing.py`), not `changes.diff`.
- The provenance records `externalParameters.factory` (package, commit and the
  definition digest, computed exactly as before so `factories.allow` pins keep
  working), `internalParameters.stages[]` (contract, harness, reported harness
  version, requested and observed models, attempts, budget, checks, outputs),
  the base tree (and commit in CI), before-image digests of every touched file,
  recorded deletions, inputs, capabilities and the APM manifest and lock.
- `apmx check` replays each stage's outputs from a producer bundle and its
  evidence. Its receipt of record carries the producer's harness and models
  with `reportedBy: producer` and names the producer bundle's digest.
- `run.yml` signs a SLSA Verification Summary v1 (subjects: the delivered
  files; `verifier.id`: the signing workflow at its commit; `policy`: the
  factory definition digest; `inputAttestations`: every bundle line;
  `verifiedLevels: []`) and attests the bundle over the same files
  (`.../attestation/receipt-bundle/v2`). The `apmx-delivery` artifact holds
  code only, plus `.apmx-deleted`.
- The attest action takes `receipt` (the bundle) and `prefix`; `checks` is gone.

### Added

- Run factories from one platform repository against many repositories.
  `run.yml` takes `target-repository` (plus `target-ref`, `target-branch`,
  `deliver`: `pull-request`, `branch` or `bundle`) and a GitHub App
  (`target-app-client-id`, `target-app-private-key`) or a `target-token`. A new `target` job fetches
  the target at its base commit and hands the tree to produce and check as an
  artifact, so the agent's job never receives a target credential. A new
  `deliver` job, with no agent and no candidate code, pushes the delivered
  files onto that commit as a branch of the target and opens or updates its
  pull request, linking the run, the attestation and the factory definition
  digest. Each job mints its own App token scoped to the one target
  repository. In hermetic CI runs the produce job proves the agent saw no
  credential. Produce and check export `APMX_BASE_REPOSITORY` and
  `APMX_BASE_COMMIT` for the provenance, and a `request` input writes the
  request text into the run root. Without `target-repository` nothing
  changes. See `examples/factory-platform` (platform workflows, organization
  policy, required-workflow ruleset, App permissions) and docs/audit.md.
- The PR gate: in a pull request, `apmx audit` looks the changed files up by
  SHA-256 in the attestation store of `factories.signers`, requires each to be
  a subject of one PASSED verification summary from an allowed signer, binds
  the statements it names, checks freshness against the PR base
  (before-images, recorded base commit) and recorded deletions. No receipt
  file in the PR. Offline: `apmx audit receipt.intoto.jsonl --outputs DIR`.
- `apmx audit --explain [factory|stages|checks|ingredients|outputs]` (replaces
  `summary.md`) and `apmx audit --export abom` (the standalone CycloneDX file).
- Runs probe `<harness> --version` once at start and record it; it never
  fails a run.

### Removed

- Applying patch outputs in the PR gate (#35): patches are applied once, at
  export, and the PR carries the resulting files.

### Security

- Pin a receipt signer to immutable workflow code. A `factories.signers` entry
  pinned only by `@refs/tags/...` or a branch trusts whatever the tag points to,
  and whoever controls the workflow's repository can move it. The new optional
  `workflow_sha: <40-hex commit>` (or `workflow: <file>.yml@<40-hex>`) must
  match the signing certificate's Build Signer Digest (Fulcio OID
  1.3.6.1.4.1.57264.1.10, `job_workflow_sha`); otherwise the Identity row is
  `[x] signed by <workflow> at <sha12>, policy requires <sha12>` and exit 1.
  For a workflow called by an annotated tag the certificate holds the tag
  object's SHA (for `run.yml@v0.6.0`, `367ca846435d`), so apmx resolves it
  read-only with the GitHub API and accepts the commit it names (`git
  rev-parse v0.6.0^{commit}`, `cadfa207e464`), or the tag object itself. A
  lookup that fails is INCOMPLETE (exit 2). A signer pinned only by a ref, or
  not at all, still passes, with the note `[!] signer pinned by mutable ref
  <ref>; pin workflow_sha`. The Identity row shows the signer's SHA, and
  `--format json` adds `signerSha`, `signerCommit`, `requiredSignerSha` and
  `signerPin` to `facts.identity`. In an `extends:` chain a child can pin a
  signer but never unpin one.

## 0.6.0 - 2026-10-21

Accept factory PRs by proof: review a factory once, then gate its pull
requests with one `apmx audit` step instead of re-reading every generated diff.

### Added

- Accept signed factory pull requests with one `factories` section in
  `apm-policy.yml` and one `apmx audit` workflow step
  (`examples/accept-factory-prs`, README "Accept factory PRs by proof"). It
  replaces the draft receiving-side gate of #10.
- `apmx audit` needs no flags. Locally it audits the newest COMPLETE run under
  `./.apm/`. In a GitHub pull request it audits the receipt the PR adds and
  binds the PR's change (merge-base..head) to it: every changed file must be a
  receipt output, or an unchanged recorded input, with the same SHA-256 at
  head. `RECEIPT_DIR`, `--outputs`, `--policy` and `--offline` override;
  `--format sarif` emits SARIF 2.1.0.
- A PR may apply a factory's patch instead of committing the `.diff`. When a
  receipt output is an `export_changes`-format patch, `apmx audit` applies it
  with strict `git apply` to the merge-base in a temporary directory, and every
  touched file at head must equal the result. A patch that does not apply, a
  partial application or an extra edit is exit 1. PRs that commit patch files
  keep the same binding.
- One policy file for APM and apmx. `apmx audit` discovers the same
  `apm-policy.yml` as `apm audit` (the repository file, read from the PR base
  in a PR, then the org policy, following `extends`) and owns one section,
  `factories`, merged tighten-only: `allow` (approved factory definition
  digests, the Factory row), `revoked` (capability content by `purl` +
  `bodySha256`, the Revocation row), `require_signed`, `signers` and
  `enforcement`. Its rules gate (exit 1) unless `factories.enforcement: warn`;
  APM's top-level `enforcement: warn` does not soften them. No policy is
  `[i] Policy  none found`; a policy that cannot be loaded is INCOMPLETE
  (exit 2). APM's own keys stay APM's, enforced in the Ingredients row.
- Know who produced a receipt. With `factories.signers`, the Identity row finds
  the receipt's GitHub artifact attestation by its `index.json` digest,
  verifies it with `gh attestation verify` against those workflows (looked up
  in the signer's `repo`), then re-checks subject, receipt and definition
  digests and the signer. `require_signed` rejects an unsigned receipt
  (exit 1); a signature that could not be checked is INCOMPLETE (exit 2).
- Keep the agent away from the checks of record and the signing identity.
  `apmx check PRODUCER_RECEIPT [FACTORY]` replays only the producer's output
  bytes (harness `replay`), reruns the factory's own checks and writes a new
  receipt of record; a different factory definition withdraws it. The reusable
  `.github/workflows/run.yml` runs produce (agent, read-only), check (no agent)
  and attest (OIDC, no candidate code) as separate jobs. `check` is now a
  reserved word.
- Run a versioned factory package in CI. `run.yml`'s `factory` accepts a
  pinned APM package reference (`owner/repo[/path]#ref`) run against the
  caller's `workspace`, like `apmx --from`; `apmx check PRODUCER_RECEIPT
  --from PACKAGE_REF` re-checks it. The definition digest binds the package's
  repository, commit and content, so a ref that moves between jobs withdraws
  the receipt; an unpinned reference is refused.
- Install a factory's check tools in CI. `run.yml`'s `checks-requirements`
  input names a requirements file inside the factory, installed into a fresh
  virtual environment whose `python3` runs the checks in the produce and check
  jobs (never in attest), with `--require-hashes` when the file is hashed. The
  check job verifies the file is the one the definition records. The
  software-factory example ships a hash-pinned `checks/requirements.txt`
  (Behave 1.3.3).
- Composite actions for CI: `.github/actions/setup` installs the native apmx
  release only if its SHA-256 matches the release's checksum file, its
  manifest and an optional pinned digest; `.github/actions/attest` signs a
  receipt after `apmx audit --offline` finds it VALID. Reference them and
  `run.yml` at `@v0.6.0` (or its commit SHA); no moving major tag is
  published.
- `scripts/demo.py prepare --factory-ref OWNER/REPO#TAG` prepares the demo kit
  against a pinned remote factory package, so the demo shows the same
  definition digest as a CI receipt pinned by a receiving policy. `factory/`
  then holds a read-only copy of the installed package, re-checked on every
  reset.

### Fixed

- `apmx --from OWNER/REPO#REF --plan` previews a Git package that is not
  installed in the calling directory instead of refusing with UNPROVEN (21).
  The bundled APM resolves it in a private temporary workspace; nothing is
  installed, no model is called and the calling directory is unchanged. A
  failed fetch (offline, unknown ref, authentication) exits 2 with what to
  check.
- Native Windows no longer halts or fails backend installation when an
  antivirus or indexer briefly locks the bundled APM pin or a freshly
  extracted APM backend. Those reads and renames retry with bounded backoff
  (about 8 seconds), then fail as before; other errors and platforms are never
  retried.

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

- apmx's internal Git operations no longer start Git's automatic
  maintenance or gc. A commit could launch a detached background job that
  changed the captured `.git` (for example `objects/maintenance.lock`) while
  apmx copied it, halting the run with exit 22; on large projects it could also
  repack the baseline mid-copy and outlive the supervised process group.
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
