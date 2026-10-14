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

- every receipt output exists at head with exactly the receipt's SHA-256, and
- every file the PR changes is either a receipt output or a recorded input
  with unchanged bytes. The receipt's own `.apm/` directory is excluded.

A PR that also edits `backdoor.py`, `apm-policy.yml` or a check script fails
the Outputs row with exit 1: `PR #7 changes backdoor.py, which the receipt
does not bind`. Use `actions/checkout` with `fetch-depth: 0` so both commits
are present; a missing commit is exit 2.

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
      # Install apmx from its release archive (see docs/install.md), then:
      - run: apmx audit
```

## Policy: one `apm-policy.yml`, discovered like `apm audit`

apmx has no policy file of its own. APM owns governance, and `apmx audit`
finds the policy the same way `apm audit` does:

1. `--policy SOURCE`, if given: `org`, `owner/repo`, an `https://` URL or a file.
2. `apm-policy.yml` at the repository's Git top level. In a pull request it is
   read from the **PR base commit**, so a PR cannot loosen its own gate. It may
   `extends: org` (or any APM policy source).
3. The organization policy APM discovers from the `origin` remote
   (`<org>/.github-private`, `.github`, `.apm`, `_apm`).

The bundled pinned APM loads and resolves that policy (`apm policy status`)
and enforces its dependency rules in the Ingredients row. Factory policy is
a new set of sections in the same file:

```yaml
# apm-policy.yml (repository or org)
enforcement: block                       # APM's switch: block | warn (default) | off
dependencies:
  deny: [acme/compromised-skill]         # existing APM rule, also checked against the receipt
security:
  integrity:
    deny_hashes: ["sha256:dead..."]      # revoke content wherever it was installed from
factories:
  allow:                                 # approved factory definitions; null = no opinion, [] = none
    - "acme/checkout-factory@sha256:0311c27f3d7b..."
  require_signed: true                   # reject receipts that are not signed (see Identity)
  signers:                               # who may sign receipts; null = no opinion, [] = none
    - workflow: acme/factories/.github/workflows/run.yml   # optional @<ref>
```

A `factories.allow` entry uses the `dependencies.allow` grammar with an
optional definition pin: `<package pattern>[@sha256:<definition digest>]`.
The digest is the receipt's `definition.json` SHA-256 (the Factory row and
`facts.definitionSha256` show it) and is what apmx matches. Receipts do not
record their factory's package identity yet, so a pinned entry's package is a
label and an unpinned pattern such as `acme/*` allows nothing; the Factory
row says so. Enforcement
follows APM: `block` makes a violation `[x]` (exit 1), `warn` makes it `[!]`
(still VALID), `off` reports the policy but applies no rules. With `extends`,
rules merge tighten-only: deny lists are unioned, `require_signed` is true if
any level sets it, and allow or signer lists keep only entries every level
allows.

| Situation | Result |
| --- | --- |
| No policy anywhere (APM outcome `absent`, `no_git_remote`, `empty` or `disabled`) | Structural audit only: `[i] Policy  none found`. Exit 0 if valid. |
| Policy found | Policy, Factory, Revocation and Identity rules apply at its enforcement level. |
| Policy found but unloadable (malformed, unreachable `extends`, an unknown `factories` key, a bad digest, or no bundled APM) | `INCOMPLETE`, exit 2. Never a pass. |
| `require_signed` and an unsigned receipt, `enforcement: block` | Identity `[x]`, exit 1. |

**Interim adapter.** The schema for these sections is defined upstream
([microsoft/apm#3232](https://github.com/microsoft/apm/pull/3232)), where
`apm policy status --json` returns the merged `effective_policy`. apmx uses
that whenever the bundled APM provides it. The bundled APM 0.33.0 does not:
it warns that `factories` is an unknown key, has no `effective_policy`, and
reports a policy containing only these sections as `empty`. Until a bundled
APM includes #3232, apmx reads only these keys (plus `dependencies.deny`,
`enforcement` and `extends`) from the exact files APM resolved, through one
isolated module (`src/apmx/audit/policy.py`), and merges `extends`
tighten-only. Remote policies are read from `github.com` repositories and
`https://` URLs. It never reads a second policy file. The swap is covered by a
test, and the rows do not change.

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
| 0 | VALID: every row passed. Warnings (`--offline`, `enforcement: warn`, an unsigned receipt without `require_signed`) still count as valid. |
| 1 | INVALID: a structural row failed, outputs differ, the PR changes files the receipt does not bind, reinstalled ingredients differ, `apm audit --ci` failed, or a blocking policy rule (allowlist, revocation, `require_signed`) rejected the receipt. |
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
| Policy | Which policy applies, from where, and its enforcement; `none found` is reported, never assumed. |
| Integrity | Every file matches `index.json` (SHA-256 and size). There are no unindexed files or symlinks, and every descriptor resolves inside the receipt. |
| Standards | Every statement is an in-toto Statement v1, validated with the upstream in-toto protobuf bindings. Production predicates are SLSA Provenance v1 and check predicates are in-toto Test Result. `abom.cdx.json` is valid against the pinned CycloneDX 1.5 JSON Schema. |
| Factory | The `definition.json` digest; producer statements agree with the definition (build type, builder, contract digest, checks, outputs, checker resources, capability bodies); capability bindings agree with the CycloneDX inventory. With `factories.allow`, the digest must be on the list. |
| Checks | Every recorded check invocation has exactly one test-result statement with consistent configuration, subjects and reports. Every check of a completed attempt passed. |
| Ingredients | Dependency trust, delegated to APM (see below). |
| Revocation | No selected capability's body or resource hash (`purl` + `bodySha256`, recorded only in the receipt) is in `security.integrity.deny_hashes`. Package hashes and `dependencies.deny` (APM's `*` and `**` globs) are APM's checks inside Ingredients; apmx applies them here only when APM did not (`--offline`, or an APM without `deny_hashes`), so a finding is never reported twice. |
| Outputs | The aggregate and per-attempt production subjects bind exactly the delivered artifacts. In a PR, the change is bound; with `--outputs`, the delivered files are re-hashed. |
| Identity | `[!] unsigned` today: receipts prove content binding, not who ran it. With `factories.require_signed`, an unsigned receipt is rejected at the policy's enforcement level. |

The CycloneDX schemas (`bom-1.5`, `spdx`, `jsf-0.82`, pinned by SHA-256 at the
upstream commit noted in `NOTICE`) ship inside apmx. Structural audit
therefore works offline.

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
accepted, but only a signature from an allowed CI workflow (`require_signed`)
says who produced it. VALID is not a sandbox, a correctness guarantee or
permission to merge or deploy.

## Independent verification

`scripts/verify_evidence.py` is a thin wrapper over the same verifier
(`src/apmx/audit/verifier.py`). It loads the verifier by file path, so it
validates a receipt **without importing APMX**; see
[evidence verification](evidence.md#independent-offline-verification).
