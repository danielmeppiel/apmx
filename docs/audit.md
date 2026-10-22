# Auditing a delivery: `apmx audit`

`apmx audit` answers one question. Someone handed you files a factory
produced: can you trust that this factory, with these dependencies, produced
them, and that its checks passed? It needs no flags:

```text
$ apmx audit
Receipt   .apm/chains/20261021T184925Z-67361d3ba1d4/receipt.intoto.jsonl   (newest COMPLETE run)   5 contracts   unsigned

[i] Policy        none found
[+] Integrity     11 statements intact and well-formed; line digests computed; evidence 31/31 blobs verified
[+] Factory       definition sha256:0bec0549b9af..  danielmeppiel/apmx-demo-factory#v1.0.0  5 contracts, 9 checks
[+] Checks        9/9 passed (in-toto test-result)
[+] Dependencies  2 APM dependencies   apm audit --ci: clean (content, lock, drift)
[i] Revocation    no policy: nothing revoked to check
[+] Outputs       4/4 delivered files match receipt subjects (re-hashed)
                  4 files bound: docs/checkout.md src/checkout.py src/pricing.py tests/test_free_shipping.py
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

Every COMPLETE run writes a [receipt](evidence.md) next to its record:
`receipt.intoto.jsonl`, `outputs/` and `evidence/` in `.apm/runs/<id>/`,
`.apm/chains/<id>/` or `.apm/controllers/<id>/`. REJECTED and HALTED runs have
no receipt. Each run's `Next` line prints the exact audit command.

## What it audits, without flags

| Where | What | Outputs | Policy |
| --- | --- | --- | --- |
| Your machine | The newest COMPLETE run's `receipt.intoto.jsonl` under `./.apm/` (run ids start with a UTC timestamp). | Its `outputs/` are re-hashed. | Discovered (below). |
| A GitHub pull request (`pull_request_target`, or `pull_request`) | The PR carries **code only**. Its changed files are looked up by SHA-256 in the attestation store of `factories.signers` (below). Only a valid factory delivery passes. | The PR's own change is bound to the signed delivery. | Discovered; the repository file is read from the PR **base**. Nothing in the PR's working tree is read. |

Ambiguity is never guessed: no receipt is exit 2 with a message saying what to pass.

### Pull requests: the gate

**Green means accepted by proof, and nothing else.** The required `audit`
check is the merge signal, so it passes only a PR that is exactly a valid
factory delivery, and auto-merge on it is safe. Every other PR fails it:

```text
[x] Identity      not a factory delivery: none of 1 changed file digest(s) carries a factory attestation in acme/factories

[x] Not a factory delivery: no changed file carries an attestation from factories.signers. This check only passes factory deliveries; merge other PRs through your normal review process (e.g. a ruleset bypass for maintainers).
```

Merge human PRs through the repository's normal path: a ruleset or branch
protection bypass for maintainers, after their usual review. Or keep
factories delivering to dedicated repositories or branches that only they
write to. apmx posts no reviews or comments.

Before verifying anything, apmx asks the store of every `factories.signers`
repository whether any added or modified file carries a factory attestation
(apmx's receipt bundle or a SLSA VSA, from any workflow). If none does, the PR
is not a factory delivery (exit 1, above). If any does, the PR must be
**exactly** a valid delivery: a delivery plus human edits fails, and so does
a human PR that copies an attested file byte-for-byte. A lookup that fails
(network, API, authentication) is INCOMPLETE, exit 2; it stops at the first
attested file, and more than 1000 lookups is INCOMPLETE too.

A factory PR (opened from `run.yml`'s `apmx-delivery`) contains only the
delivered files, no receipt folder. In a PR, apmx reads the event's base and
head commits, diffs `merge-base..head`, and:

1. hashes every added or modified file at head;
2. asks GitHub for the attestations stored for those digests, like npm
   provenance (`gh api repos/<signer repo>/attestations/sha256:<digest>`),
   and verifies them with `gh attestation verify <file> --bundle <bundles>
   --repo <signer repo> --signer-workflow <workflow> --predicate-type
   https://slsa.dev/verification_summary/v1 --cert-oidc-issuer
   https://token.actions.githubusercontent.com --deny-self-hosted-runners`,
   for each `factories.signers` entry (Sigstore signature, transparency log,
   certificate). The list is fetched unfiltered because GitHub's list API
   refuses the VSA predicate type as a filter;
3. requires **one** PASSED SLSA VSA from an allowed signer, at its
   `workflow_sha` when pinned, whose `verifier.id` names that workflow at the
   signing commit, and whose subjects include **every** changed file at its
   repository path with the same SHA-256;
4. fetches the receipt bundle the same signer attested over the same files
   and keeps exactly the statements the VSA's `inputAttestations` name by
   digest, then runs every structural and policy row on them;
5. **freshness:** every file the PR touches must have, at the PR base, the
   before-image the factory started from (or be absent, for a file it
   created). A recorded base `gitCommit` must be in the PR base's history.
   Otherwise: `built on a stale base: src/pricing.py is different at base
   1a2b3c4d5e6f; rerun the factory`;
6. **deletions:** a file the PR deletes must be in the provenance's recorded
   deletions, with its before-image at base.

A factory PR that also edits `backdoor.py`, `apm-policy.yml` or a check script
has no summary covering it: `[x] Identity  no PASSED verification summary from
factories.signers covers every changed file (backdoor.py is not a delivered
file)`, exit 1. Delivered files the PR leaves out are noted, not required.
Use `actions/checkout` with `fetch-depth: 0` so both commits are present; a
missing commit is exit 2. Nothing from the PR is executed or read as
configuration: apmx hashes the PR's bytes from Git objects, reads
`apm-policy.yml` from the base commit (never the working tree, even when the
base has none), runs the bundled APM for policy discovery in an empty project
that shares only the `origin` remote, refuses a `--policy` file inside the
checkout, and reinstalls dependencies from the attested lock in a temporary
project with `APM_NO_SCRIPTS=1`.

The gate needs `factories.signers`: without them there is nothing to look
attestations up from, and the result is INCOMPLETE (exit 2).

```yaml
# .github/workflows/audit.yml in the receiving repository
on:
  pull_request_target:            # runs the base branch's copy of this file
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  attestations: read
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}   # bytes to hash; never executed
          fetch-depth: 0
          persist-credentials: false
      - uses: danielmeppiel/apmx/.github/actions/setup@v0.7.1   # installs apmx 0.7.1
      - run: apmx audit
        env:
          GH_TOKEN: ${{ github.token }}   # gh attestation verify
```

### A PR cannot rewrite its own gate

Use `pull_request_target`, never `pull_request`. A `pull_request` workflow
runs the **PR's own copy** of the workflow file, so a PR can edit it to `exit
0`, or delete it. `pull_request_target` runs the copy on the base branch.
It runs with the base repository's token and secrets, which is why the job
above has read-only permissions, checks the PR head out without credentials,
and runs nothing but `apmx audit`: no scripts, no package installs, nothing
from the PR.

What was measured on `danielmeppiel/apmx-demo-shop` (branch protection
requiring the check `audit` from GitHub Actions, October 2026):

| PR | Check runs named `audit` on the PR head | Merge state |
| --- | --- | --- |
| Gate on `pull_request`; the PR adds a `pull_request` job named `audit` that exits 0 | gate failure, spoof success (also when the spoof ran last) | `BLOCKED` |
| Gate on `pull_request`; the PR also deletes its copy of `audit.yml` | spoof success only | `CLEAN`: **mergeable** |
| Gate on `pull_request_target`; the PR adds the spoof and rewrites its `audit.yml` to exit 0 | base gate failure, spoof success, rewritten copy success | `BLOCKED` |

GitHub requires every check run with the required name (from the expected
source) to pass, so an extra same-name success cannot override the base
gate's failure. Pin the required check's expected source to **GitHub
Actions** so no other app can report it. Repository rulesets and branch
protection pin a required check to an app, not to a workflow file: any
workflow in the repository can report a check named `audit`. That is safe
only while the base gate always runs and reports, which
`pull_request_target` guarantees; do not put an `if:` on the job (a skipped
required job counts as passed). Whoever can change the gate on the default
branch can still change the gate.

For organizations, prefer an **organization ruleset with *Require workflows
to pass***, pointing at the gate in a central repository pinned to a commit
([`examples/factory-platform`](../examples/factory-platform)): the
repository's own maintainers cannot edit or remove it, and its run is
identified by workflow file, not by a check name.

The setup action downloads the native release archive for the runner and
installs it only if its SHA-256 matches the release's `.sha256` file, the
release manifest and, if you give one, the `sha256` input. Reference the
actions and `run.yml` by an immutable release tag such as `@v0.7.1`, or by
that tag's commit SHA. apmx publishes no moving major tag (`@v1`).

**Offline route.** Off GitHub, ship the bundle with the files and run
`apmx audit receipt.intoto.jsonl --outputs DIR`: the same structural and
policy rows, with the files re-hashed. In a PR, an explicit bundle binds the
PR's change to it instead (without `--outputs`).

## Policy: one `apm-policy.yml`, one apmx section

apmx has no policy file of its own. It reads the same `apm-policy.yml` that
`apm audit` uses, found the same way:

1. `--policy SOURCE`, if given: `org`, `owner/repo`, an `https://` URL or a file.
2. `apm-policy.yml` at the repository's Git top level. In a pull request it is
   read from the **PR base commit**, so a PR cannot loosen its own gate; a
   policy the PR adds is ignored. It may `extends: org` (or any APM policy
   source).
3. The organization policy repository APM discovers from the `origin` remote
   (`.github-private`, `.github`, `.apm`, `_apm`).

APM keeps owning its own keys: the Dependencies row runs `apm audit --ci
--policy` with that same policy. apmx owns exactly one top-level section,
`factories`, and reads it from the raw document itself. It reuses the bundled
APM only to locate the policy and to learn whether APM can load it
(`apm policy status --json`); it never reads or reinterprets APM's keys,
apart from `extends` and a top-level `enforcement: off`.

```yaml
# apm-policy.yml (repository or org)
enforcement: block                       # APM's own switch for APM's rules
dependencies:
  deny: [acme/compromised-skill]         # APM's rule, enforced in the Dependencies row
factories:                               # apmx's section
  allow:                                 # approved definitions; null = no opinion, [] = none
    - "acme/checkout-factory@sha256:0311c27f3d7b..."
  require_signed: true                   # reject deliveries that are not signed (see Identity)
  signers:                               # who may sign; null = no opinion, [] = none
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml   # optional @<ref>
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886   # git rev-parse v0.7.1^{commit}
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
  is the SHA-256 of the receipt's canonical factory definition, named by the
  provenance's `externalParameters.factory.definition` (the Factory row and
  `facts.definitionSha256` show it), and it is what apmx matches. A pinned
  entry's package is a label; an unpinned pattern such as `acme/*` allows
  nothing yet, and the Factory row says so.
- `signers`: `workflow: owner/repo/.github/workflows/<file>.yml`, optionally
  pinned to the signer's ref with `@<ref>` (for example `@refs/tags/v0.7.1`), an
  optional `workflow_sha`, and an optional `repo: owner/repo`: the repository
  the factory ran in, where its attestations are stored. `repo` defaults to the
  repository being audited.
- `workflow_sha` (40 hex) pins the signer workflow's code. Whoever controls the
  workflow's repository can move a tag or a branch, so `@<ref>` alone does not
  pin the reviewed `run.yml`; `workflow_sha` does. Pin the **commit**: `git
  rev-parse v0.7.1^{commit}`; for apmx's `run.yml@v0.7.1` that is
  `a70c1939dddc6f5d7888b76fd5bb8bab3e69e886`. `workflow: <file>.yml@<40-hex sha>` is the
  same pin. apmx compares it with the signing certificate's Build Signer Digest
  (Fulcio extension OID `1.3.6.1.4.1.57264.1.10`, the OIDC `job_workflow_sha`
  claim; `gh attestation verify --format json` shows it as
  `buildSignerDigest`). For a workflow called by commit, that digest is the
  commit. For a workflow called by an annotated tag, GitHub reports the tag
  object's SHA instead (for `run.yml@v0.6.0`, `367ca846435d34f2e89f870335eeb709f3954c49`). apmx then
  resolves that tag object read-only with `gh api
  repos/<owner>/<repo>/git/tags/<sha>`, and the pin matches if the tag names
  that commit; the row says `at <commit12> (tag object <sha12>)`. Pinning the
  tag object itself (`git rev-parse v0.7.1`) also works, without the lookup;
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
| Policy found | Its `factories` rules apply; APM's rules apply in Dependencies. |
| Policy found but unloadable (malformed, unreachable `extends`, an unknown `factories` key, a bad digest, or no bundled APM) | `INCOMPLETE`, exit 2. Never a pass. |
| `require_signed` and an unsigned delivery | Identity `[x]`, exit 1 (`[!]` with `factories.enforcement: warn`). |

Remote policies and `extends` parents are read from `github.com` repositories
(GitHub contents API with your `GITHUB_APM_PAT`, `GITHUB_TOKEN` or `GH_TOKEN`)
and `https://` URLs. Any other host is unloadable (exit 2).

## Command

```text
apmx audit [RECEIPT] [--policy SOURCE] [--outputs DIR] [--offline] [--format text|json|sarif]
apmx audit [RECEIPT] --explain [factory|stages|checks|dependencies|outputs]
apmx audit [RECEIPT] --export abom [-o FILE]
```

| Option | Overrides |
| --- | --- |
| `RECEIPT` | A `receipt.intoto.jsonl` (or its run directory). In a PR, the PR's change is bound to it, relative to its `<root>/.apm/` workspace or the repository root. |
| `--policy SOURCE` | Policy discovery. A relative file is resolved from your current directory. In a PR, a file inside the checkout is refused (exit 2): it is the PR's own copy. |
| `--outputs DIR` | Re-hash the delivered files in `DIR` against the subjects (instead of the PR binding). |
| `--offline` | Network use. APM is not launched and nothing is fetched: Dependencies reads `not audited (offline)`, signatures `not checked (offline)`. Only a local policy file is read. |
| `--format json` | Machine output: `{"schema": "apmx-audit/2", "receipt", "selection", "result", "exitCode", "reason", "rows", "facts", "signed"}`. `facts.stages` lists each stage's harness, version and models. |
| `--format sarif` | SARIF 2.1.0 for GitHub code scanning: one result per `[x]` (error) or `[!]` (warning) row. |
| `--explain [TOPIC]` | Print what the receipt records (factory, stages, checks, dependencies, outputs; default all), after it verifies structurally. `factory` names the standards the statements are schema-valid against. Replaces v1's `summary.md`. |
| `--export abom` | Write the inline CycloneDX 1.5 inventory as a standalone file for SBOM tools (stdout, or `-o FILE`; never overwrites). |

`audit` is a reserved word. To run a local directory named `audit`, write `./audit`.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | VALID: every row passed. Warnings (`--offline`, `factories.enforcement: warn`, an unsigned local receipt without `require_signed`) still count as valid. |
| 1 | INVALID: a structural row failed, outputs differ, a PR is not a factory delivery, the PR changes files no signed delivery covers or is built on a stale base, reinstalled dependencies differ, `apm audit --ci` failed, or a `factories` rule rejected the delivery. |
| 2 | Not a pass: usage error, missing receipt, a PR change that could not be read, a policy that could not be loaded, a PR gate without signers, attestations that could not be looked up, or dependencies or signatures that could not be checked (network or authentication). |

The final line is `VALID`, `INVALID <first reason>` or `INCOMPLETE <reason>`;
for a PR that is not a factory delivery it is the `[x] Not a factory
delivery: ...` message above (`--format json`: `"result": "invalid"`, that
message as `reason`, and `facts.delivery`).
Output is printable ASCII and uses the `[+]`, `[x]`, `[!]` and `[i]` symbols.
Colour appears only on a terminal, and never when `NO_COLOR` is set.

## Rows

Structural rows (Integrity, Factory, Checks, Outputs) run in order
and stop at the first failure. Later rows show `[i] not evaluated`, and APM is
never launched for a receipt that is already invalid.

| Row | What it checks |
| --- | --- |
| Policy | Which policy applies, from where, and the `factories` enforcement; `none found` is reported, never assumed. |
| Integrity | Every line is a canonical unsigned DSSE envelope of an in-toto statement; one provenance, one CycloneDX, at most one VSA, whose `inputAttestations` bind exactly the other lines by SHA-256. Every `evidence/sha256/<hex>` blob present matches its name (evidence is optional). Then every statement is well-formed against its standard: in-toto Statement v1 (upstream protobuf bindings), SLSA Provenance v1, in-toto Test Result v0.1, SLSA VSA v1, and CycloneDX 1.5 against the pinned JSON Schema. A schema failure is an Integrity failure. |
| Factory | Build type `.../build/factory/v2`, the factory definition digest and stages; with the definition in `evidence/`, its stages, checks and outputs agree; capability bindings agree with the CycloneDX inventory. With `factories.allow`, the digest must be on the list. |
| Checks | One PASSED Test Result per declared check of the final candidate, whose configuration names the check, command, stage and definition. |
| Dependencies | Dependency trust, delegated to APM (see below). |
| Revocation | No selected capability matches a `factories.revoked` entry (exact `purl` and `bodySha256`). |
| Outputs | Every statement names the same delivered files; before-images, creations and deletions are consistent. Locally the files are re-hashed; in a PR the change is bound (gate above). |
| Identity | With `factories.signers`, a signed VSA over the delivered files from those workflows (below). Without signers, `[!]`: content binding only. |

The CycloneDX schemas (`bom-1.5`, `spdx`, `jsf-0.82`, pinned by SHA-256 at the
upstream commit noted in `NOTICE`) ship inside apmx, so structural audit works offline.

## Signed deliveries

`run.yml`'s attest job signs with GitHub artifact attestations (Sigstore,
keyless, the job's OIDC token) using
[`.github/actions/attest`](../.github/actions/attest/action.yml). It runs
`apmx audit --offline` on the receipt of record from an empty directory (no
policy or PR context changes what is signed), requires complete evidence, and
creates two attestations over the delivered files:

- the receipt bundle, predicate type
  `https://github.com/danielmeppiel/apmx/attestation/receipt-bundle/v2`
  (`{"lines": [statement, ...]}`), so the statements can be fetched by a
  file's digest. Signing it asserts nothing by itself. Each statement is not
  stored on its own because GitHub's attestation store refuses a SLSA
  provenance whose `buildType` it does not recognize;
- a **SLSA Verification Summary v1**, the statement the gate trusts:

```json
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "src/pricing.py", "digest": {"sha256": "e6981e53..."}}, "..."],
 "predicateType": "https://slsa.dev/verification_summary/v1",
 "predicate": {
   "verifier": {"id": "https://github.com/danielmeppiel/apmx/.github/workflows/run.yml@<job_workflow_sha>"},
   "timeVerified": "2026-10-21T18:52:30Z",
   "resourceUri": "git+https://github.com/acme/shop@<base commit>",
   "policy": {"uri": "apmx:factory/0bec0549...", "digest": {"sha256": "0bec0549..."}},
   "inputAttestations": [{"uri": "receipt.intoto.jsonl#1", "digest": {"sha256": "<line 1>"}}, "..."],
   "verificationResult": "PASSED",
   "verifiedLevels": []}}
```

Subjects are the delivered files at their **repository** paths (the run's
workspace prefix applied). `verifiedLevels` is empty on purpose: apmx claims
no SLSA level. `inputAttestations` is the standard mechanism that replaces
v1's `index.json`. Any file can be checked with stock tooling: `gh
attestation download src/pricing.py --repo <repo>`, then `gh attestation
verify src/pricing.py --repo <repo> --bundle sha256:<digest>.jsonl
--signer-workflow <workflow> --predicate-type
https://slsa.dev/verification_summary/v1`.

| Summary | Identity row | With `require_signed` |
| --- | --- | --- |
| Verified from an allowed signer | `[+] signed by <workflow>@<ref> at <sha12>`; final line `VALID (authenticated: <workflow>)` | pass |
| Verified, signer pinned only by `@<ref>` or not pinned | as above, plus `[!] signer pinned by mutable ref <ref>; pin workflow_sha` | pass (a warning) |
| Verified from an allowed workflow, but not at its `workflow_sha` | `[x] signed by <workflow> at <sha12>, policy requires <sha12>`, exit 1 (`[!]` with `factories.enforcement: warn`) | same |
| Signed by an annotated tag object that could not be resolved | `[!] signer commit could not be checked against workflow_sha`, INCOMPLETE, exit 2 | same |
| None that covers these files (locally: `[!] unsigned`; in a PR: `[x]`, exit 1, or `Not a factory delivery` when no changed file carries any factory attestation) | `[!] unsigned: no attestation from factories.signers` | `[x]`, exit 1 |
| Could not be checked (no `gh`, network, auth, `--offline`) | `[!] signature could not be checked` | INCOMPLETE, exit 2 |

`--format json` records the signer in `facts.identity`: `signer`, `signerRef`,
`signerSha` (the certificate's Build Signer Digest), `signerCommit`,
`requiredSignerSha`, `signerPin`, `sourceRepository` and `run`.

**Assumptions.** Verification trusts `gh attestation verify` and the Sigstore
public-good instance it uses, GitHub's OIDC issuer for workflow identity, and
the GitHub REST API to name the commit of a tag-object signer digest. A
`signers` entry trusts every run of that workflow file unless `workflow_sha`
pins its code. The summary says which workflow vouched for a structurally VALID
receipt with complete evidence; it does not make the producing agent
trustworthy. Harness and model names are what the harness reported in the
producer job (`reportedBy: producer`): as trustworthy as the harness version
`run.yml` installs.

## Receipts from CI: `run.yml` and `apmx check`

An agent runs as the runner user, so in its own job it could rewrite check
scripts or results, and it could use a job's OIDC token to sign anything. The
reusable workflow [`.github/workflows/run.yml`](../.github/workflows/run.yml)
keeps the agent away from both:

| Job | Permissions | Runs |
| --- | --- | --- |
| produce | `contents: read` | `apmx FACTORY`: the agent. Its own checks are only a fast fail; its bundle and evidence are uploaded as untrusted data. |
| check | `contents: read` | A fresh runner and the same commit, no agent: `apmx check PRODUCER_RECEIPT FACTORY` (or `--from PACKAGE_REF`). Uploads `apmx-receipt` and a code-only `apmx-delivery`. |
| attest | `id-token: write`, `attestations: write` | No checkout of the caller's code: `apmx audit --offline`, then the bundle and the verification summary. |

```yaml
jobs:
  factory:
    uses: danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1
    with:
      factory: path/to/factory
      checks-requirements: checks/requirements.txt   # optional, inside the factory
    permissions: { contents: read, id-token: write, attestations: write }
    secrets:
      agent-token: ${{ secrets.COPILOT_TOKEN }}   # seen only by the produce job
```

`run.yml`'s `apmx-version` and `apmx-ref` inputs default to the same release,
so its jobs install that apmx and use the setup and attest actions from that
tag. If you call `run.yml` by a commit SHA, pass the same SHA as `apmx-ref`.

`factory` can also be an APM package reference pinned to a tag or commit,
`owner/repo[/path]#ref`. The package runs against the caller's `workspace`
directory (default: the repository root), exactly like `apmx --from`. The
check job resolves the same reference again; the definition digest records
the package's repository, resolved commit and content, so if the reference
moved between the jobs, the receipt is withdrawn.

`apmx check PRODUCER_RECEIPT [FACTORY] [--from PACKAGE_REF]` takes only the
output bytes of each stage from the producer's bundle and `evidence/`,
matched to contracts by SHA-256, and ignores its checks, records and claims.
It runs FACTORY from this checkout with a `replay` producer that writes those
bytes instead of running an agent; the factory's own checks run on them, and a
new receipt of record is written. Its stages carry the producer's harness,
harness version and models, labelled `reportedBy: producer` and `checkedBy:
apmx check (replay)`, and the producer bundle's digest is an input. It must
have the producer's factory definition digest; otherwise it is withdrawn and
the command exits 1. A self-consistent forged producer receipt whose outputs
fail the checks is REJECTED there.

Checks run with `python3` from `PATH`. A factory whose checks need Python
packages names its own approved requirements file in `checks-requirements`.
`run.yml` installs it into a fresh virtual environment in the produce and
check jobs only (`--require-hashes` when the file has hashes), and the check
job requires it to be the `checks/` resource the factory definition records.

`apmx-delivery` holds exactly the delivered files at their repository paths,
plus `.apmx-deleted` (one path per line) for files the factory deleted: a
control file for the step that opens the PR, never to commit. apmx's own CI
runs `run.yml` end to end with the hermetic protocol actor (no model calls) in
`.github/workflows/run-selftest.yml`, signs with this repository's OIDC
identity, commits the code-only delivery as a PR would, and checks it with
`apmx audit` from the attestation store alone.

### Deployment: one factory platform, many repositories

At scale, one platform repository runs approved factories against many
repositories and opens code-only pull requests there
([`examples/factory-platform`](../examples/factory-platform)). Call `run.yml`
with `target-repository` (and optionally `target-ref`, default its default
branch; `request`, the request text written into the run root):

```yaml
    with:
      factory: "acme/checkout-factory#v1.0.0"   # must be a pinned package
      target-repository: acme/shop
      request: ${{ inputs.request }}
      target-app-client-id: ${{ vars.FACTORY_APP_CLIENT_ID }}
    secrets:
      agent-token: ${{ secrets.COPILOT_TOKEN }}
      target-app-private-key: ${{ secrets.FACTORY_APP_PRIVATE_KEY }}
```

| Job | Credential | Runs |
| --- | --- | --- |
| target | App token, *Contents: read*, this repository only | Fetches the target at its base commit without persisting credentials and passes the tree (no `.git`) on as an artifact. |
| produce, check | none for the target | Run on that tree. `APMX_BASE_REPOSITORY` and `APMX_BASE_COMMIT` name the base for the provenance. |
| attest | OIDC | Unchanged: attestations are stored in the platform repository. |
| deliver | App token, *Contents* and *Pull requests: write*, this repository only | No agent and no apmx: commits the `apmx-delivery` files onto the base commit, force-pushes `target-branch` (default `apmx/run-<run id>`, never the base or default branch) and opens or updates its pull request, linking the run, the attestation and the factory definition digest. `deliver: branch` only pushes; `deliver: bundle` needs no token and only uploads the commit as the `apmx-delivered` git bundle, which every mode uploads. |

The App's private key is a secret only the target and deliver jobs reference,
and each mints its own token, so the agent's job never receives a target
credential (a GitHub App token cannot be passed between jobs, which is why
`run.yml` mints them). `target-token` is an alternative for a token you
already have, such as `GITHUB_TOKEN` when the target is the calling
repository. The deliver job refuses paths in `.git` and under
`.github/workflows/`, symbolic links, and writes through a symbolic link in
the target. Each target's gate pins the platform as the signer:

```yaml
  signers:
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886   # or the tag object SHA
      repo: acme/factory-platform
```

Without `target-repository`, `run.yml` behaves as before: the caller's
repository, no deliver job. `run-selftest-platform.yml` exercises the
platform model with this repository as both platform and target. It uses
`deliver: bundle` and pushes from its own job, because a `GITHUB_TOKEN` passed
to `run.yml` becomes each called job's own read-only token.

## Dependencies: APM owns dependency trust

apmx does not reimplement dependency auditing. The provenance carries the
run's exact `apm.yml` and `apm.lock.yaml` inline (`resolvedDependencies` with
`content`, bound by digest), so this row works in a PR with no receipt file.
For an audit, apmx:

1. Checks that the retained lock projects to the definition's
   `resolvedDependencies` (when the definition is present in `evidence/`; the
   signed summary vouches for it otherwise). With zero dependencies, the row reads
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
dependencies are unavailable (exit 2). Remote dependencies need network and
package access with your normal APM authentication.

## What VALID does not mean

A local receipt is **unsigned**. VALID then means the receipt is internally
consistent and its files are bound by content to this factory definition,
these dependencies and these check results. Anyone who can rewrite every file
and hash can forge a self-consistent receipt; `factories.allow` limits which
definitions are accepted, and only a summary signed by an allowed CI workflow
(`require_signed` with `signers`) says who produced it. VALID is not a sandbox,
a correctness guarantee or permission to merge or deploy.

## Independent verification

`scripts/verify_evidence.py` is a thin wrapper over the same verifier
(`src/apmx/audit/verifier.py`). It loads the verifier by file path, so it
validates a receipt **without importing APMX**; see
[evidence verification](evidence.md#independent-offline-verification).
