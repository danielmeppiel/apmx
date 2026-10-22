---
title: "Policy: the factories section of apm-policy.yml"
description: The keys APMX reads from apm-policy.yml, their validation and defaults, discovery order, extends merging and offline behavior.
---

## Where it lives

APMX has no policy file of its own. It reads APM's `apm-policy.yml` and owns
exactly one top-level key, `factories`. Of APM's keys it reads only
top-level `enforcement` (only `off` matters) and `extends`.

APM 0.33.0 warns `Unknown top-level policy key: 'factories'`. That is
expected.

## Keys

All keys sit under `factories:`.

| Key | Type | Default when absent | Validation | Meaning |
| --- | --- | --- | --- | --- |
| `allow` | list of non-empty strings | no opinion: any definition passes the Factory row | Each entry is `<package pattern>[@sha256:<64 lowercase hex>]`. A pattern with `sha256:` anywhere else is rejected. | Only `name@sha256:<definition digest>` entries allow anything; the name is a label. Unpinned patterns such as `acme/*` allow nothing. `[]` allows none. |
| `require_signed` | boolean | `false` | Must be `true` or `false`. | Unsigned or unverifiable deliveries fail Identity (exit 1), or are INCOMPLETE when the signature cannot be checked. |
| `signers` | list of mappings | no opinion | Each item has `workflow` (required), and optionally `repo` and `workflow_sha`; no other keys. `workflow` is `[host/]owner/repo/.github/workflows/<file>.yml` or `.yaml`, optionally `@<ref>`. `repo` is `owner/repo`. `workflow_sha` is 40 hex characters. `workflow: x.yml@<40 hex>` means the same as `workflow_sha`, and both must agree. | The workflows allowed to sign. `repo` is where the factory ran and its attestations are stored (default: the repository being audited). `@<ref>` pins the certificate's ref, which can move. `workflow_sha` pins the signer's commit; the annotated tag object's SHA is also accepted. |
| `revoked` | list of mappings | none | Each item has exactly `purl` (`pkg:...`) and `bodySha256` (64 lowercase hex). | A skill is revoked only when both match exactly. |
| `enforcement` | `block` or `warn` | `block` | Other values are rejected. | `warn` turns Factory, Revocation and Identity violations from `[x]` into `[!]`, for a staged rollout. APM's own top-level `enforcement: warn` does not affect it. |

Any validation error makes the whole policy unloadable: the audit is
INCOMPLETE (exit 2), never a pass.

## Example

```yaml title="apm-policy.yml"
# apm-policy.yml at the root of the repository that receives factory PRs
factories:
  allow:
    # Definition digest of the factory you reviewed (shown by `apmx audit` Factory row / facts.definitionSha256).
    - "danielmeppiel/apmx-demo-factory#v1.0.0@sha256:0bec0549b9af1e093c5204610163203ca6a58ea7602ad1021cdd381c042b039e"
  require_signed: true
  signers:
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.7.1   # optional @ref
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886                  # git rev-parse v0.7.1^{commit}
      repo: acme/factories            # repository where run.yml ran (attestations stored there)
  revoked: []
  enforcement: block
```

The repository's examples (`examples/accept-factory-prs/apm-policy.yml`,
`examples/factory-platform/apm-policy.yml`) ship an all-zero `allow` digest
as a placeholder. Replace it with your factory's definition digest: with the
placeholder, every delivery fails the Factory row.

## Top-level enforcement: off

APM's top-level `enforcement: off` disables every `factories` rule:

- Policy: `[i] <label>   factories off`, with the note
  `top-level enforcement: off disables factories rules`.
- Revocation: `[i] not evaluated (top-level enforcement: off)`.
- Identity: treated as if there were no policy.

A policy with no `factories` section shows
`[+] Policy  <label>   no factories section` and
`[i] Revocation  policy has no factories section`.

## Discovery and precedence

1. `--policy SOURCE`: a file relative to the current directory, `org`,
   `owner/repo`, `github.com/owner/repo` or an `https://` URL. The prefixes
   `file:`, `url:`, `org:` and `repo:` are accepted and stripped.
2. `apm-policy.yml` at the top of the Git checkout.
3. The organization policy APM discovers from the `origin` remote (in a
   `.github-private`, `.github`, `.apm` or `_apm` repository).
4. Nothing: `[i] Policy  none found`, with a note such as
   `no apm-policy.yml in <dir> and no git origin for org policy`.

In a pull request, step 2 reads the file from the **PR base commit** only,
never from the PR. A `--policy` file inside the checkout is refused, so the
audit is INCOMPLETE:

```text title="apmx audit --policy apm-policy.yml in a PR (main, trimmed to the row)"
[!] Policy        apm-policy.yml (--policy) could not be loaded: in a pull request, --policy must not name a file in the checkout: that is the PR's own copy
                  A policy that cannot be loaded is never treated as a pass.
```

## extends

`extends:` chains are followed, up to 5 levels, with no cycles
(`extends: org` is located by APM). The levels merge so a child can only
tighten:

| Key | Merged across levels |
| --- | --- |
| `allow` | intersection of the levels that set it |
| `signers` | intersection of the levels that set it; a child may pin a signer its parent lists unpinned, never unpin one |
| `require_signed` | true if any level says true |
| `revoked` | union |
| `enforcement` | `warn` only if every level that sets it says `warn` |

## Remote policies

- `owner/repo` is read through the GitHub contents API, with the first token
  set of `GITHUB_APM_PAT`, `GITHUB_TOKEN`, `GH_TOKEN`.
- `https://` URLs are fetched directly, up to 1 MiB.
- Any other host makes the policy unloadable.

Both forms were verified against `danielmeppiel/apmx-demo-shop`:
`--policy danielmeppiel/apmx-demo-shop` and
`--policy https://raw.githubusercontent.com/danielmeppiel/apmx-demo-shop/main/apm-policy.yml`.

## Offline

With `--offline`:

- A local file is read directly, without APM. An `extends` is not followed:
  the Policy row warns that the policy is partial (still VALID).
- Organization discovery reports `[!] org policy not checked (offline)`.
- A remote `--policy` is a usage error (exit 2):
  `--offline can only read a local --policy file; org, owner/repo and URLs need the network.`

## APM's own rules

APM's keys are enforced by APM, inside the audit's Dependencies row
(`apm audit --ci --policy <source>`), and only when APM reports the policy as
`found`, that is, when it has APM rules. A policy with only a `factories`
section is reported by APM 0.33.0 as `empty`, and `apm audit` then runs with
`--no-policy`. JSON shows which: `facts.apmAudit.policy` is
`"none (--no-policy)"` for the demo shop's factories-only policy.

:::note[Since the next release]
That audit row is named Dependencies. In v0.7.1 it is named Ingredients.
:::

## What each key protects

- `allow`: a weakened check or contract changes the definition digest, which
  is then not on the list.
- `require_signed` with `signers`: a hand-written or forged receipt has no
  summary from your signer. Audited online; offline it is INCOMPLETE.
- `workflow_sha`: a moved tag cannot change which `run.yml` code signs. A
  signer without `workflow_sha` trusts every run of that workflow file.
- `revoked`: a skill found to be bad stops counting, by exact content.

The full picture is in the [trust model](/apmx/docs/trust-model/#what-it-stops-and-under-which-policy).
