# Auditing a receipt: `apmx audit`

`apmx audit` answers one question. Someone handed you outputs and a receipt:
can you trust that this factory, with these ingredients, produced these files,
and that its checks passed? It needs no flags:

```text
$ apmx audit
Receipt   .apm/chains/20261009T090958Z-7a55db0b0bb0/receipt   (newest COMPLETE run)   2 contracts   unsigned

[i] Policy        none found
[+] Integrity     all 26 files match index.json (SHA-256)
[+] Standards     in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5: schema-valid
[+] Factory       definition sha256:0311c27f3d7b..  2 contracts, 2 checks
[+] Checks        2/2 passed (in-toto test-result)
[+] Ingredients   0 APM dependencies   nothing to audit
[i] Revocation    no policy: nothing revoked to check
[+] Outputs       3 files bound: final.json first.json second.json
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

Every COMPLETE run delivers a [receipt](evidence.md) next to its record:
`.apm/runs/<id>/receipt`, `.apm/chains/<id>/receipt` or
`.apm/controllers/<id>/receipt`. REJECTED and HALTED runs have no receipt.
Each run's `Next` line prints the exact audit command for its receipt.

## What it audits, without flags

| Where | Receipt | Outputs | Policy |
| --- | --- | --- | --- |
| Your machine | The newest COMPLETE run under `./.apm/` (run ids start with a UTC timestamp). | Checked inside the receipt. | Discovered (below). |
| A GitHub pull request (`pull_request` or `pull_request_target`) | The one receipt the PR adds under `.apm/<runs\|chains\|controllers>/<id>/receipt`. | The PR's own change is bound to the receipt (below). | Discovered; the repository file is read from the PR **base**. |

Ambiguity is never guessed. No receipt, or a PR that adds several, is exit 2
with a message saying what to pass.

### Pull requests: the change is the claim

In a PR, apmx reads the event's base and head commits and diffs
`merge-base..head`. The audit passes only when:

- every receipt output exists at head with exactly the receipt's SHA-256, or
  is a patch output the PR applies (below), and
- every file the PR changes is a receipt output, a file an applied patch
  touches, or a recorded input with unchanged bytes. The receipt's own
  `.apm/` directory is excluded.

A PR that also edits `backdoor.py`, `apm-policy.yml` or a check script fails
the Outputs row with exit 1: `PR #7 changes backdoor.py, which the receipt
does not bind`. Use `actions/checkout` with `fetch-depth: 0` so both commits
are present; a missing commit is exit 2.

**Applied patches.** A factory that delivers code as a patch (the
`export_changes` tool, for example `changes.diff`) should open a PR with the
patch applied, not the `.diff` file. An output is a *patch output* when the
receipt's own indexed bytes (never the PR's copy) are a `git diff
--full-index` text patch and every file it modifies or deletes has, as its
pre-image, the exact bytes the producing attempt captured in its baseline.
Patch paths are relative to the receipt's workspace, the directory that holds
`.apm/`. Each patch output is delivered one of two ways:

- **committed**: the PR adds or modifies `<workspace>/<name>`, or head already
  has it with the receipt's digest. It is an ordinary output, bound by SHA-256,
  and the files it touches must not change.
- **applied**: otherwise. apmx copies the merge-base bytes of the touched
  files into a temporary directory outside the checkout, checks each against
  the patch's recorded pre-image, runs `git apply` (no fuzz, no 3-way) and
  requires head to equal the result for every touched file: bytes, mode,
  additions and deletions. Patches apply in attempt order.

Once any patch is applied, the other outputs are optional: those the PR
changes must match their digest, and the rest stay in the receipt. Without
an applied patch, every output must be at head, as before. Committing a patch
file and also applying it fails. So do a patch that does not apply to the
base (`changes.diff does not apply to base 1a2b3c4d5e6f: src/app.py differs
from the patch pre-image`), a patched file left unchanged (`PR #7 does not
apply changes.diff to src/app.py (unchanged from base)`), a missing hunk or an
extra edit (`src/app.py at head differs from changes.diff applied to base`),
and a touched path that is absolute, contains `..`, or lies under `.git`,
`.gitmodules` or `.apm`, or is a symlink or submodule at base or head. Nothing
from the PR is executed. In `--format json`, `facts.change.patches` lists each
patch output with its delivery (`applied` or `committed`) and files, and
`facts.change.appliedFiles` lists the files the applied patches touch.

```yaml
# .github/workflows/audit.yml in the receiving repository
on: pull_request
permissions:
  contents: read
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: danielmeppiel/apmx/.github/actions/setup@v0.6.0   # installs apmx 0.6.0
      - run: apmx audit
        env:
          GH_TOKEN: ${{ github.token }}   # gh attestation verify, for the Identity row
```

The setup action downloads the native release archive for the runner and
installs it only if its SHA-256 matches the release's `.sha256` file, the
release manifest and, if you give one, the `sha256` input. Pin `sha256` to
trust a digest you reviewed rather than the release's own checksum files.

Reference the actions and `run.yml` by an immutable release tag such as
`@v0.6.0`, or by that tag's commit SHA. apmx publishes no moving major tag
(`@v1`). At a release tag, the setup action's default `version` is that
release.

## Policy: one `apm-policy.yml`, one apmx section

apmx has no policy file of its own. It reads the same `apm-policy.yml` that
`apm audit` uses, found the same way:

1. `--policy SOURCE`, if given: `org`, `owner/repo`, an `https://` URL or a file.
2. `apm-policy.yml` at the repository's Git top level. In a pull request it is
   read from the **PR base commit**, so a PR cannot loosen its own gate. It may
   `extends: org` (or any APM policy source).
3. The organization policy repository APM discovers from the `origin` remote
   (`.github-private`, `.github`, `.apm`, `_apm`).

APM keeps owning its own keys: the Ingredients row runs `apm audit --ci
--policy` with that same policy. apmx owns exactly one top-level section,
`factories`, and reads it from the raw document itself. It reuses the bundled
APM only to locate the policy and to learn whether APM can load it
(`apm policy status --json`); it never reads or reinterprets APM's keys,
apart from `extends` and a top-level `enforcement: off`.

```yaml
# apm-policy.yml (repository or org)
enforcement: block                       # APM's own switch for APM's rules
dependencies:
  deny: [acme/compromised-skill]         # APM's rule, enforced in the Ingredients row
factories:                               # apmx's section
  allow:                                 # approved definitions; null = no opinion, [] = none
    - "acme/checkout-factory@sha256:0311c27f3d7b..."
  require_signed: true                   # reject receipts that are not signed (see Identity)
  signers:                               # who may sign receipts; null = no opinion, [] = none
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml   # optional @<ref>
      workflow_sha: cadfa207e4645306199ddb85271c5f3e13340924   # immutable pin: git rev-parse v0.6.0^{commit}
      repo: acme/factories                 # where the factory ran (default: this repository)
  revoked:                               # capability content revoked by incident response
    - purl: "pkg:github/acme/style@3f2a..."
      bodySha256: "dead..."
  enforcement: block                     # block (default) or warn, for a staged rollout
```

APM 0.33.0 prints `Unknown top-level policy key: 'factories'`. That warning
is expected and harmless. APM 0.33.0 also reports a policy whose only section
is `factories` as `empty`; apmx still applies its rules and runs
`apm audit` without a policy, because APM has none to enforce.

- `allow`: entries use the `dependencies.allow` grammar with an optional
  definition pin, `<package pattern>[@sha256:<definition digest>]`. The digest
  is the receipt's `definition.json` SHA-256 (the Factory row and
  `facts.definitionSha256` show it), and it is what apmx matches. Receipts do
  not record their factory's package identity yet, so a pinned entry's
  package is a label and an unpinned pattern such as `acme/*` allows nothing
  for now; the Factory row says so.
- `signers`: `workflow: owner/repo/.github/workflows/<file>.yml`, optionally
  pinned to the signer's ref with `@<ref>` (for example `@refs/tags/v0.6.0`), an
  optional `workflow_sha`, and an optional `repo: owner/repo`: the repository
  the factory ran in, where its attestations are stored. `repo` defaults to the
  repository being audited.
- `workflow_sha` (40 hex) pins the signer workflow's code. Whoever controls the
  workflow's repository can move a tag or a branch, so `@<ref>` alone does not
  pin the reviewed `run.yml`; `workflow_sha` does. Pin the **commit**: `git
  rev-parse v0.6.0^{commit}` (for apmx's `run.yml@v0.6.0`,
  `cadfa207e4645306199ddb85271c5f3e13340924`). `workflow: <file>.yml@<40-hex sha>` is the
  same pin. apmx compares it with the signing certificate's Build Signer Digest
  (Fulcio extension OID `1.3.6.1.4.1.57264.1.10`, the OIDC `job_workflow_sha`
  claim; `gh attestation verify --format json` shows it as
  `buildSignerDigest`). For a workflow called by commit, that digest is the
  commit. For a workflow called by an annotated tag, GitHub reports the tag
  object's SHA instead (for `run.yml@v0.6.0`, `367ca846435d34f2e89f870335eeb709f3954c49`). apmx then
  resolves that tag object read-only with `gh api
  repos/<owner>/<repo>/git/tags/<sha>`, and the pin matches if the tag names
  that commit; the row says `at <commit12> (tag object <sha12>)`. Pinning the
  tag object itself (`git rev-parse v0.6.0`) also works, without the lookup;
  it hashes its target commit, so it is just as immutable. If the lookup
  fails, the audit is INCOMPLETE (exit 2), never a pass. Re-pin when you upgrade
  the `run.yml` you call.
- `revoked`: a capability is revoked only when both its `purl` and its
  `bodySha256` match exactly. The same content stays revoked when it is
  reinstalled from another path, and a `purl` alone never revokes.
- **Enforcement:** `factories` rules are gate rules. A violated `allow`,
  `require_signed`, `revoked` or signer `workflow_sha` rule is `[x]` and exit 1. APM's top-level
  `enforcement: warn` does **not** soften them; a policy that cannot be loaded
  is never treated as a pass either (the
  [microsoft/apm#3195](https://github.com/microsoft/apm/issues/3195) lesson).
  `factories.enforcement: warn` makes violations `[!]` for a staged rollout.
  Only a top-level `enforcement: off` disables them, and the Policy row says
  `factories off`.
- **`extends`:** apmx merges its own section across the chain, tighten-only and
  in any order. `allow` and `signers` keep only the entries every level that
  sets them allows, `require_signed` is true if any level sets it, `revoked`
  is the union, and `factories.enforcement` is `warn` only if every level that
  sets it says `warn`. A level allows a signer entry when it lists the same
  `workflow` and `repo` with no `@<ref>` or `workflow_sha` the entry lacks: a
  child can pin a signer its parent lists unpinned, but it cannot unpin one
  (an unpinned child entry is dropped and the parent's pinned entry is kept),
  and two different pins allow nothing.

| Situation | Result |
| --- | --- |
| No policy anywhere (APM reports `absent`, `no_git_remote` or `disabled`, or an `empty` policy without `factories`) | Structural audit only: `[i] Policy  none found`. Exit 0 if valid. |
| Policy found | Its `factories` rules apply; APM's rules apply in Ingredients. |
| Policy found but unloadable (malformed, unreachable `extends`, an unknown `factories` key, a bad digest, or no bundled APM) | `INCOMPLETE`, exit 2. Never a pass. |
| `require_signed` and an unsigned receipt | Identity `[x]`, exit 1 (`[!]` with `factories.enforcement: warn`). |

Remote policies and `extends` parents are read from `github.com` repositories
(GitHub contents API with your `GITHUB_APM_PAT`, `GITHUB_TOKEN` or `GH_TOKEN`)
and `https://` URLs. Any other host is unloadable (exit 2).

## Command

```text
apmx audit [RECEIPT_DIR] [--policy SOURCE] [--outputs DIR] [--offline] [--format text|json|sarif]
```

Every option overrides a default:

| Option | Overrides |
| --- | --- |
| `RECEIPT_DIR` | Receipt selection. In a PR, the change is still bound, relative to the receipt's `<root>/.apm/` workspace, or to the repository root. |
| `--policy SOURCE` | Policy discovery. A relative file is resolved from your current directory. |
| `--outputs DIR` | The PR binding: re-hash the delivered files in `DIR` against the receipt's output subjects instead. Locally, outputs are otherwise checked only inside the receipt. |
| `--offline` | Network use. APM is not launched and nothing is fetched: Ingredients reads `not audited (offline)`, an org policy reads `not checked (offline)` and an unresolved `extends` is a warning. Only a local policy file is read. |
| `--format json` | Machine output: `{"schema": "apmx-audit/1", "receipt", "selection", "result", "exitCode", "reason", "rows", "facts", "signed": false}`. |
| `--format sarif` | SARIF 2.1.0 for GitHub code scanning: one result per `[x]` (error) or `[!]` (warning) row. |

`audit` is a reserved word. `apmx <factory-dir | x.contract.md | package-ref>`
is still the run shorthand. To run a local directory named `audit`, write
`./audit`.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | VALID: every row passed. Warnings (`--offline`, `factories.enforcement: warn`, an unsigned receipt without `require_signed`) still count as valid. |
| 1 | INVALID: a structural row failed, outputs differ, the PR changes files the receipt does not bind, reinstalled ingredients differ, `apm audit --ci` failed, or a `factories` rule (allowlist, revocation, `require_signed`) rejected the receipt. |
| 2 | Not a pass: usage error, missing or ambiguous receipt, a PR change that could not be read, a policy that could not be loaded, or ingredients that could not be audited (network or authentication). Rerun with access, or use `--offline` to skip the network explicitly. |

The final line is `VALID`, `INVALID <first reason>` or `INCOMPLETE <reason>`.
Output is printable ASCII and uses the `[+]`, `[x]`, `[!]` and `[i]` symbols.
Colour appears only on a terminal, and never when `NO_COLOR` is set.

## Rows

Structural rows (Integrity, Standards, Factory, Checks, Outputs) run in order
and stop at the first failure. Later rows show `[i] not evaluated`, and APM is
never launched for a receipt that is already invalid.

| Row | What it checks |
| --- | --- |
| Policy | Which policy applies, from where, and the `factories` enforcement; `none found` is reported, never assumed. |
| Integrity | Every file matches `index.json` (SHA-256 and size). There are no unindexed files or symlinks, and every descriptor resolves inside the receipt. |
| Standards | Every statement is an in-toto Statement v1, validated with the upstream in-toto protobuf bindings. Production predicates are SLSA Provenance v1 and check predicates are in-toto Test Result. `abom.cdx.json` is valid against the pinned CycloneDX 1.5 JSON Schema. |
| Factory | The `definition.json` digest; producer statements agree with the definition (build type, builder, contract digest, checks, outputs, checker resources, capability bodies); capability bindings agree with the CycloneDX inventory. With `factories.allow`, the digest must be on the list. |
| Checks | Every recorded check invocation has exactly one test-result statement with consistent configuration, subjects and reports. Every check of a completed attempt passed. |
| Ingredients | Dependency trust, delegated to APM (see below). |
| Revocation | No selected capability matches a `factories.revoked` entry (exact `purl` and `bodySha256`, which only the receipt records). Package-level rules such as `dependencies.deny` stay APM's, in the Ingredients row. |
| Outputs | The aggregate and per-attempt production subjects bind exactly the delivered artifacts. In a PR, the change is bound: outputs match at head (`PR #7: 3/3 outputs match at head ..`) or patch outputs applied to the merge-base reproduce head (`PR #7: changes.diff + documentation.diff applied to base reproduce head (5 files)`); with `--outputs`, the delivered files are re-hashed. |
| Identity | With `factories.signers`, the receipt's GitHub artifact attestation is verified against those workflows (see [Signed receipts](#signed-receipts)). Without signers, `[!]`: the receipt proves content binding, not who ran it. With `factories.require_signed`, an unsigned receipt is rejected (exit 1), and one whose signature could not be checked is INCOMPLETE. A receipt signed by an allowed workflow at a commit other than its `workflow_sha` is rejected (exit 1); a signer pinned only by a tag or branch passes with a `[!]` note. |

The CycloneDX schemas (`bom-1.5`, `spdx`, `jsf-0.82`, pinned by SHA-256 at the
upstream commit noted in `NOTICE`) ship inside apmx. Structural audit
therefore works offline.

## Signed receipts

A receipt is signed in CI by
[`.github/actions/attest`](../.github/actions/attest/action.yml). The action
runs `apmx audit --offline` on the receipt from an empty directory, so no
policy or PR context changes what is signed, and refuses anything that is not
VALID. It then creates a GitHub artifact attestation (Sigstore, keyless, via
the job's OIDC token) whose subject is the SHA-256 of the receipt's
`index.json`, which binds every other receipt file. The predicate,
`https://github.com/danielmeppiel/apmx/attestation/receipt/v1`, records the
receipt digest, the factory definition digest, the outputs, and whether the
checks ran in a separate job (`separate-job`) or the agent's own job
(`same-job`).

```yaml
  attest:   # a job that runs no agent and no candidate code
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      attestations: write
    steps:
      - uses: danielmeppiel/apmx/.github/actions/setup@v0.6.0
      - uses: actions/download-artifact@v4
        with: { name: receipt, path: receipt }
      - uses: danielmeppiel/apmx/.github/actions/attest@v0.6.0
        with: { receipt: receipt, checks: separate-job }
```

`apmx audit` finds the attestation by that digest, the way `npm audit
signatures` does, so nobody passes it a signature. For each
`factories.signers` entry it runs `gh attestation verify index.json --repo
<repo> --signer-workflow <workflow> --predicate-type <apmx> --cert-oidc-issuer
https://token.actions.githubusercontent.com --deny-self-hosted-runners`. It
then re-checks the verified statement itself: the subject and predicate name
this receipt's digest and definition, the certificate's signer is that
workflow (at the entry's `@<ref>` and `workflow_sha`, if pinned), and the runner was
GitHub-hosted. Attestations are looked up in the signer's `repo`, the
repository the factory ran in, which is where a reusable `run.yml` called from
it stores them; `gh attestation verify --repo` also pins that source
repository. Without `repo`, the repository being audited (`GITHUB_REPOSITORY`,
or the `origin` remote) is used. `gh` needs a token
(`GH_TOKEN: ${{ github.token }}` in Actions).

| Attestation | Identity row | With `require_signed` |
| --- | --- | --- |
| Verified from an allowed signer | `[+] signed by <workflow>@<ref> at <sha12>`; final line `VALID (authenticated: <workflow>)` | pass |
| Verified, signer pinned only by `@<ref>` or not pinned | as above, plus the note `[!] signer pinned by mutable ref <ref>; pin workflow_sha` (or `[!] signer not pinned (any ref); pin workflow_sha`) | pass (a warning, exit 0, even with `factories.enforcement: block`) |
| Verified from an allowed workflow, but not at its `workflow_sha` | `[x] signed by <workflow> at <sha12>, policy requires <sha12>`, exit 1 with or without `require_signed` (`[!]` with `factories.enforcement: warn`) | same |
| Signed by an annotated tag object that could not be resolved to compare with `workflow_sha` | `[!] signer commit could not be checked against workflow_sha`, INCOMPLETE, exit 2 with or without `require_signed` | same |
| None, or none that matches this receipt | `[!] unsigned: no attestation from factories.signers` | `[x]`, exit 1 (`[!]` with `factories.enforcement: warn`) |
| Could not be checked (no `gh`, network, auth, `--offline`) | `[!] signature could not be checked` | INCOMPLETE, exit 2 |

`--format json` records the signer in `facts.identity`: `signer`, `signerRef`,
`signerSha` (the certificate's Build Signer Digest), `signerCommit` (the commit
a tag-object digest names, when apmx resolved it), `requiredSignerSha` (the
entry's `workflow_sha`, or `null`), `signerPin` (`workflow_sha`, `ref` or
`none`), `sourceRepository` and `run`.

**Assumptions.** Verification trusts `gh attestation verify` (GitHub CLI) and
the Sigstore public-good instance it uses, and GitHub's OIDC issuer for
workflow identity, and the GitHub REST API to name the commit of a tag-object
signer digest. A `signers` entry trusts every run of that workflow file
unless `workflow_sha` pins its code; `@<ref>` (for example a release tag) narrows
which ref ran, but a tag or branch can be moved. The signature says which
workflow vouched for a structurally VALID receipt. It does not make the
producing agent trustworthy; the producer workflow must keep the agent out of
the signing job (see `run.yml`).

## Receipts from CI: `run.yml` and `apmx check`

An agent runs as the runner user, so in its own job it could rewrite check
scripts or results, and it could use a job's OIDC token to sign anything. The
reusable workflow [`.github/workflows/run.yml`](../.github/workflows/run.yml)
keeps the agent away from both:

| Job | Permissions | Runs |
| --- | --- | --- |
| produce | `contents: read` | `apmx FACTORY`: the agent. Its own checks are only a fast fail; its receipt is untrusted and is uploaded as data. |
| check | `contents: read` | A fresh runner and the same commit, no agent: `apmx check PRODUCER_RECEIPT FACTORY` (or `--from PACKAGE_REF`). |
| attest | `id-token: write`, `attestations: write` | No checkout of the caller's code: `apmx audit --offline`, then the [attest action](#signed-receipts) with `checks: separate-job`. |

```yaml
jobs:
  factory:
    uses: danielmeppiel/apmx/.github/workflows/run.yml@v0.6.0
    with:
      factory: path/to/factory
      checks-requirements: checks/requirements.txt   # optional, inside the factory
    permissions: { contents: read, id-token: write, attestations: write }
    secrets:
      agent-token: ${{ secrets.COPILOT_TOKEN }}   # seen only by the produce job
```

`run.yml`'s `apmx-version` and `apmx-ref` inputs default to the same release
(`0.6.0`, `v0.6.0`), so its jobs install that apmx and use the setup and
attest actions from that tag. If you call `run.yml` by a commit SHA, pass the
same SHA as `apmx-ref`. A signer entry can pin the tag with
`workflow: danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.6.0`, but
a tag can move: add `workflow_sha: cadfa207e4645306199ddb85271c5f3e13340924`,
the v0.6.0 commit (see `factories.signers` above).

`factory` can also be an APM package reference pinned to a tag or commit,
`owner/repo[/path]#ref`. The package runs against the caller's `workspace`
directory (default: the repository root), exactly like `apmx --from`. The
workspace is its input and evidence root, and the delivery overlay is laid
out under it. The check job resolves the same reference again. The definition
digest records the package's repository, resolved commit and content, so if
the reference moved between the jobs, the receipt is withdrawn. An unpinned
reference is refused.

```yaml
    with:
      factory: "acme/checkout-factory#v1.0.0"   # reviewed once, versioned on its own
      checks-requirements: checks/requirements.txt  # inside the package
```

`apmx check PRODUCER_RECEIPT [FACTORY] [--from PACKAGE_REF]` takes only the producer receipt's
output bytes, matched to contracts by SHA-256, and ignores its checks,
records and claims. It runs FACTORY (default: the current directory) from this
checkout with a `replay` producer that writes those bytes instead of running an
agent. The factory's own checks run on them, and a new receipt of record is
written. That receipt records the harness as `replay`, never the producer's
model. It must have the producer's factory definition digest; otherwise it is
withdrawn and the command exits 1. A self-consistent forged producer receipt
whose outputs fail the checks is REJECTED there. With `--from`, the package
runs against the current directory and the receipt is written under `./.apm/`.
`check` is a reserved word, like `audit`.

Checks run with `python3` from `PATH`. A factory whose checks need Python
packages names its own approved requirements file in `checks-requirements`
(a path inside the factory or factory package). `run.yml` installs it into a fresh virtual
environment and puts that environment's `python3` first on `PATH`, in the
produce and check jobs only; the attest job runs no factory code. When the
file contains any `--hash`, pip runs with `--require-hashes`. The file is part
of the factory directory, so changing it is a change to the factory you review.
The check job also requires the installed file to be the `checks/` resource
that the receipt's factory definition records.

`run.yml` uploads `apmx-receipt` (the receipt of record) and `apmx-delivery`
(the outputs at their paths plus the receipt under `FACTORY/.apm/`, or under
`WORKSPACE/.apm/` for a package), ready to
commit as a pull request that `apmx audit` accepts by proof. apmx's own CI
runs it end to end with the hermetic protocol actor (no model calls) in
`.github/workflows/run-selftest.yml`, signs with this repository's OIDC
identity, and verifies that signature with `apmx audit`.
`run-selftest-package.yml` runs a fixture package pinned to the tested commit.

## Ingredients: APM owns dependency trust

apmx does not reimplement dependency auditing. The receipt retains the run's
exact `apm.yml` and `apm.lock.yaml`. For an audit, apmx:

1. Checks that the retained lock projects to the definition's
   `resolvedDependencies`. With zero dependencies, the row reads
   `0 APM dependencies` and nothing is launched.
2. Stages the manifest and lock in a fresh temporary project and runs the
   **bundled, pinned** APM: `apm install --frozen --only apm --target
   agent-skills --no-trust-bin --no-policy`. Scripts are disabled
   (`APM_NO_SCRIPTS=1`); no receipt code runs.
3. Confirms that the reinstall recorded the same content hashes as the
   receipt's lock. For changed local-path packages, `apm install --frozen`
   rewrites lock hashes from whatever it finds instead of failing
   ([microsoft/apm#3194](https://github.com/microsoft/apm/issues/3194)), so
   apmx compares them and then restores the retained lock bytes. Deployed file
   hashes are compared by path rather than by owning dependency, because APM
   0.33.0 can attribute a skill shared by a package and its transitive local
   dependency to either entry on replay. It also checks that every selected
   skill body matches the receipt's `bodySha256`, and that the pinned APM
   derives exactly the receipt's CycloneDX components from the retained lock.
4. Runs `apm audit --ci --no-fail-fast` against the retained lock. This covers
   hidden-Unicode content scanning, lock consistency, install-replay drift
   and, with `--policy`, org policy.
5. Deletes the temporary project.

**Policy decision.** When no policy is discovered, the audit runs with
`--no-policy` and the Policy row says so. When one is discovered, apmx passes
that same source to `apm audit --ci --policy` and requires evidence that APM
actually enforced it. APM skips enforcement with only a warning and exits 0
when an explicit policy cannot be loaded
([microsoft/apm#3195](https://github.com/microsoft/apm/issues/3195)), so apmx
reports such a run as `INCOMPLETE` (exit 2), never as a pass. The temporary
audit project gets your `origin` remote, so `org` resolves the same way it
does in your repository.

Local-path dependencies resolve on the auditor's machine at the absolute
paths recorded in the lock. When those paths do not exist there, the
ingredients are unavailable (exit 2). Remote dependencies need network and
package access with your normal APM authentication.

## What VALID does not mean

The receipt is **unsigned**. VALID means the receipt is internally consistent
and that its files are bound by content to this factory definition, these
ingredients and these check results. It does not authenticate who ran the
factory. Anyone who can rewrite every file and hash can forge a
self-consistent receipt; `factories.allow` limits which definitions are
accepted, and only a signature from an allowed CI workflow (`require_signed`
with `signers`) says who produced it. VALID is not a sandbox, a correctness guarantee or
permission to merge or deploy.

## Independent verification

`scripts/verify_evidence.py` is a thin wrapper over the same verifier
(`src/apmx/audit/verifier.py`). It loads the verifier by file path, so it
validates a receipt **without importing APMX**; see
[evidence verification](evidence.md#independent-offline-verification).
