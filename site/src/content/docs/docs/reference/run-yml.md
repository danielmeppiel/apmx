---
title: run.yml and the setup and attest actions
description: Inputs, secrets, outputs, permissions, jobs and artifacts of APMX's reusable workflow, and the setup and attest actions.
---

## Call it

```yaml title=".github/workflows/factory.yml (job)"
jobs:
  run:
    uses: danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1
    permissions: { contents: read, id-token: write, attestations: write }
    with:
      factory: danielmeppiel/apmx-demo-factory#v1.0.0
      request: "What the factory should build"
    secrets:
      agent-token: ${{ secrets.COPILOT_TOKEN }}
```

- The caller grants `contents: read, id-token: write, attestations: write`.
  `run.yml` itself declares top-level `permissions: {}` and grants each job
  its own subset.
- Call it by release tag. APMX publishes no moving major tag.

A walk-through is in [run a factory in CI](/apmx/docs/run-in-ci/).

## Inputs

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `factory` | string | `"."` | A factory directory in the caller repository, or an APM package pinned to a tag or commit, `owner/repo[/path]#ref`, run against `workspace` like `apmx --from`. |
| `workspace` | string | `"."` | The caller directory a package factory runs against (its inputs and evidence root). Ignored for a local factory directory. |
| `harness` | string | `copilot` | The agent CLI: `copilot` or `opencode`. It must be installable on ubuntu-24.04; `run.yml` installs only the Copilot CLI. |
| `model` | string | `""` | Passed to the agent CLI; empty means its default. |
| `apmx-version` | string | `"0.7.1"` | The APMX release to install. `source` is for APMX's own CI. |
| `apmx-ref` | string | `v0.7.1` | The ref of `danielmeppiel/apmx` that provides the setup and attest actions. Keep it equal to the `run.yml` ref you call. |
| `attest` | boolean | `true` | Sign the receipt of record. Needs `id-token: write` and `attestations: write`. |
| `checks-requirements` | string | `""` | A pip requirements file inside the factory or package (for example `checks/requirements.txt`), installed into a fresh virtual environment in `produce` and `check` only. Hashes are required when the file has any. |
| `hermetic-actor` | boolean | `false` | APMX's own CI only: replace the agent CLI with a hermetic fixture (no model). |
| `request` | string | `""` | Text written to `request-file` in the run root before the factory runs (in `produce` and `check`). Untrusted prompt input. |
| `request-file` | string | `request.md` | The path inside the run root for `request`. |
| `target-repository` | string | `""` | Platform mode: the `owner/name` the factory runs against and delivers to. `factory` must then be a pinned package. Empty: the caller repository, and no `deliver` job. |
| `target-ref` | string | `""` | The branch, tag or commit of the target to build on. Empty: its default branch. |
| `target-branch` | string | `""` | The branch `deliver` force-pushes; never the base or default branch. Empty: `apmx/run-<run id>`. |
| `deliver` | string | `pull-request` | `pull-request` (push and open or update a PR), `branch` (push only), or `bundle` (no push, no token). Every mode uploads the `apmx-delivered` bundle. |
| `target-app-client-id` | string | `""` | A GitHub App client ID. The `target` and `deliver` jobs mint their own installation tokens, scoped to the one target repository. |

## Secrets

All optional.

| Secret | Used by | Meaning |
| --- | --- | --- |
| `agent-token` | `produce` only | The agent CLI token, exported as `COPILOT_GITHUB_TOKEN`. |
| `target-app-private-key` | `target`, `deliver` | The GitHub App's private key. |
| `target-token` | `target`, `deliver` | A token for the target, instead of the App. |

## Outputs

| Output | Value |
| --- | --- |
| `definition-sha256` | the factory definition digest |
| `receipt-sha256` | the receipt of record's digest |
| `attestation-url` | the signed verification summary; empty when `attest` is off |
| `base-commit` | the commit the factory built on |
| `delivered-commit` | the delivered commit |
| `pull-request-url` | the pull request |

## Jobs

The core is three isolated jobs: the agent works (`produce`), checks verify
(`check`), a trusted signer signs (`attest`). All jobs run on
`ubuntu-24.04`, each on a fresh runner.

| Job | Needs | Permissions | Timeout (min) | Agent | Credentials it sees | What it does |
| --- | --- | --- | --- | --- | --- | --- |
| `target` | - | `contents: read` | 10 | no | a read-only App token for the target (or `target-token`, or `github.token`) | Validates `owner/name`. In platform mode, checks out the target at `target-ref` without persisting credentials and uploads its tree (no `.git`) as `apmx-target`. Outputs the repository and commit. |
| `produce` | `target` | `contents: read` | 60 | **yes** | `agent-token` only | Checks out the caller (or unpacks the target tree), installs APMX and, only for `harness: copilot`, the Copilot CLI. Stages the factory outside Git, writes `request`, installs check requirements, runs `apmx` with `--on <harness> --allow-host-access --allow-unproven-inputs`, and uploads its receipt as `apmx-producer-receipt`. Its checks are only a fast fail; its receipt is untrusted. |
| `check` | `target`, `produce` | `contents: read` | 30 | no | none | On a fresh runner and the same commit, stages the factory again from the caller or target, downloads `apmx-producer-receipt` and runs [`apmx check`](/apmx/docs/reference/check/). Verifies the installed check requirements match the definition. Packages `apmx-receipt` and `apmx-delivery`. |
| `attest` | `check`; only if `attest` | `contents: read`, `id-token: write`, `attestations: write` | 15 | no | OIDC | No caller checkout. Installs APMX, downloads `apmx-receipt` and runs the [attest action](#attest-action). This is the trusted signer. |
| `deliver` | `target`, `check`, `attest`; only with `target-repository`, after `check` succeeded and `attest` succeeded or was off | `contents: read` | 15 | no | an App token with contents and pull-requests write (or `target-token`) | Commits the delivery onto the base commit (Git with no hooks, filters or global config). Refuses `.git`, `.github/workflows/` and symlinks; never pushes the base or default branch. Force-pushes `target-branch`, opens or updates the PR per `deliver`, and uploads `apmx-delivered`. |

`deliver` runs no candidate code.

## Artifacts

| Artifact | Contents | Retention |
| --- | --- | --- |
| `apmx-target` | the target tree, without `.git` (platform mode) | 1 day |
| `apmx-producer-receipt` | the agent job's receipt and evidence; untrusted | 7 days |
| `apmx-receipt` | the receipt of record, with `evidence/` and `outputs/` | default |
| `apmx-delivery` | the delivered files at their repository paths, plus `.apmx-deleted` | default |
| `apmx-delivered` | the delivered commit as a git bundle | default |

## Delivery modes

| `deliver` | Push | Pull request | Write token |
| --- | --- | --- | --- |
| `pull-request` | yes | opened or updated | needed |
| `branch` | yes | no | needed |
| `bundle` | no | no | not needed |

GitHub rebinds a passed `GITHUB_TOKEN` to each called job's own read-only
token, and pull requests opened with `GITHUB_TOKEN` do not trigger other
workflows. Without a GitHub App or another write token, use `bundle` and push
the bundle from a job of your own.

## setup action

`danielmeppiel/apmx/.github/actions/setup@v0.7.1` installs the native APMX
release on a runner.

| Input | Default | Meaning |
| --- | --- | --- |
| `version` | `"0.7.1"` | The release to install. |
| `sha256` | `""` | Optional: the expected archive digest for this runner's target. |
| `token` | `${{ github.token }}` | Used only to avoid rate limits. |

Outputs: `path`, `sha256`, `version`.

It downloads `apmx-<version>-<target>.tar.gz` (or `.zip`), its `.sha256` file
and `release-manifest.json`. The archive must match both, and the `sha256`
input when given. It extracts to the tool cache and adds APMX to `PATH`; it
does not use pip.

Targets: `linux-x86_64`, `linux-arm64`, `macos-x86_64`, `macos-arm64`,
`windows-x86_64`. Published v0.7.1 digests:

| Archive | SHA-256 |
| --- | --- |
| `apmx-0.7.1-linux-x86_64.tar.gz` | `222fd72b969191493c5c437ffc1f9542e93ca98bd04a806ede7e8138680129e9` |
| `apmx-0.7.1-macos-arm64.tar.gz` | `75dd467db41c37b3e122a402b5717c1972718671ed8d09fd841cd051828e2297` |

## attest action

`.github/actions/attest` is what the `attest` job runs. Callers do not
reference it directly.

| Input | Default | Meaning |
| --- | --- | --- |
| `receipt` | required | Path to `receipt.intoto.jsonl`, with `evidence/` beside it. |
| `prefix` | `""` | The repository directory of the delivered files. |
| `github-token` | `${{ github.token }}` | |

Outputs: `definition-sha256`, `attestation-id`, `attestation-url` (of the
verification summary).

It refuses to sign unless:

1. the bundle has canonical **unsigned** lines and no verification summary
   yet;
2. `apmx audit <bundle> --offline --format json`, run from an empty temporary
   directory with `GITHUB_ACTIONS` and `GITHUB_EVENT_*` removed, exits 0 with
   `result: valid`, so no repository policy or PR context influences what is
   signed;
3. `facts.evidence` is `complete`;
4. the audited line digests equal the bundle's.

It then prints
`[+] receipt is VALID with complete evidence; signing a PASSED verification summary of N file(s) for definition <12>..`
and creates two GitHub artifact attestations (Sigstore keyless, with the
job's GitHub OIDC identity), both with the delivered files at their
repository paths as subjects:

| Attestation | Predicate type | Predicate |
| --- | --- | --- |
| receipt bundle | `https://github.com/danielmeppiel/apmx/attestation/receipt-bundle/v2` | `{"lines": [...]}`: the receipt's statements, in line order. It lets `apmx audit` fetch them by file digest; signing it asserts nothing by itself. |
| verification summary | `https://slsa.dev/verification_summary/v1` | `verifier.id` (the workflow at its commit, from the OIDC token), `timeVerified`, `resourceUri` (the base repository at the base commit), `policy` (the definition digest), `inputAttestations` (every receipt line by digest), `verificationResult: "PASSED"`, `verifiedLevels: []`. |

The statements travel inside the bundle predicate because GitHub's store
refuses a SLSA provenance with an unknown `buildType`.

## Limits

- **The signing-time audit is offline and applies no policy.** At signing,
  dependencies are not re-audited (`not audited (offline)` is VALID) and no
  `factories` rule applies. The trusted signer audits offline; your gate
  re-audits dependencies online and applies your policy.
- **`run.yml` does not install OpenCode.** It installs only the Copilot CLI.
- **The model token is visible to `produce`**, the agent's job.
- **The `check` job runs your checks on agent-written bytes** with host
  access. It has no write token and no signing identity.

`.github/actions/setup-from-source` exists for APMX's own CI
(`apmx-version: source`).
