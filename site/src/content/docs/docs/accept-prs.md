---
title: Accept factory PRs by proof
description: Review the factory once. Its pull requests carry code only and merge on one required check, signed in CI by a workflow the agent cannot reach.
---

Review the factory once, then accept its pull requests by proof instead of
re-reading every generated diff. The receiving side is one policy section and
one workflow step.

## 1. Produce in CI

The factory repository calls the reusable
[`run.yml`](https://github.com/danielmeppiel/apmx/blob/main/.github/workflows/run.yml)
(`danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1`). Each job runs on a
fresh machine:

| Job | Has the agent | Model token | Write token | Can sign |
| --- | --- | --- | --- | --- |
| `target` | no | no | read only (App) | no |
| `produce` | **yes** | **yes** | no | no |
| `check` | no, reruns the checks | no | no | no |
| `attest` | no | no | no | **yes**, after `apmx audit --offline` is VALID |
| `deliver` | no | no | **yes** (target only) | no |

The attest job signs a SLSA Verification Summary over the delivered files with
the workflow's GitHub OIDC identity through Sigstore. The pull request carries
**code only**; never commit a receipt.

## 2. Pin what you accept

Copy [`apm-policy.yml`](https://github.com/danielmeppiel/apmx/blob/main/examples/accept-factory-prs/apm-policy.yml)
to the receiving repository. APM keeps its own keys; APMX reads only
`factories`.

```yaml title="apm-policy.yml"
factories:
  allow:
    # The definition digest of the factory you reviewed (the Factory row shows it).
    - "acme/checkout-factory@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  require_signed: true
  signers:
    # apmx's reusable run.yml, pinned to the commit your factory calls
    # (`git rev-parse v0.7.1^{commit}`). A tag can move; this SHA cannot.
    # Re-pin when you upgrade the run.yml your factory calls.
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886   # git rev-parse v0.7.1^{commit}
      repo: acme/factories   # where the factory ran: its attestations are stored there
  revoked: []
```

## 3. Gate the PR

Add [`audit.yml`](https://github.com/danielmeppiel/apmx/blob/main/examples/accept-factory-prs/audit.yml)
and make its `audit` check required, with GitHub Actions as the source. It
runs on `pull_request_target`, so the base branch's copy runs and a PR cannot
edit or delete its own gate. It checks the PR head out as bytes, without
credentials, and executes nothing from it.

```yaml title=".github/workflows/audit.yml (steps)"
- uses: danielmeppiel/apmx/.github/actions/setup@v0.7.1   # installs apmx 0.7.1
- run: apmx audit
  env: { GH_TOKEN: "${{ github.token }}" }
```

**Green means accepted by proof, and nothing else**, so auto-merge on the
check is safe.

## 4. A real pair

Both ran against the public [apmx-demo-shop](https://github.com/danielmeppiel/apmx-demo-shop)
on v0.7.1. [PR #11](https://github.com/danielmeppiel/apmx-demo-shop/pull/11)
is a factory delivery; [PR #12](https://github.com/danielmeppiel/apmx-demo-shop/pull/12)
is the same delivery plus a 4-line hand edit to `src/checkout.py`.

```text title="PR #11: audit (pull_request_target)"
Receipt   attestations for PR #11   (looked up by file digest)   1 contract   signed

[+] Policy        apm-policy.yml (repository, PR base 4fed7ee21e1d)   factories enforcement block
[+] Integrity     4 statements intact and well-formed; line digests computed; the VSA binds every other line; evidence not present (optional)
[+] Factory       definition sha256:c011c35b5790.. on factories.allow (danielmeppiel/apmx/tests/fixtures/run-workflow-package)
[+] Checks        1/1 passed (in-toto test-result)
[+] Dependencies  1 APM dependency   apm audit --ci: clean (content, lock, drift)
[+] Revocation    0 of 0 capabilities revoked; factories.revoked is empty
[+] Outputs       PR #11: 1 changed file = delivered files at head d85c418bdf11; before-images match base 4fed7ee21e1d
[+] Identity      signed by danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.7.1 at a70c1939dddc (tag object 917ffb4e5406)

[+] VALID   (authenticated: danielmeppiel/apmx/.github/workflows/run.yml)
```

```text title="PR #12: the same delivery plus one hand edit"
[+] Policy        apm-policy.yml (repository, PR base 4fed7ee21e1d)   factories enforcement block
[i] Integrity     not evaluated (no signed delivery covers this PR)
...
[x] Identity      no PASSED verification summary from factories.signers covers every changed file (src/checkout.py is not a delivered file)

[x] INVALID   Identity: no PASSED verification summary from factories.signers covers every changed file (src/checkout.py is not a delivered file)
##[error]Process completed with exit code 1.
```

:::caution[Hermetic rehearsal]
In this pair a stand-in fixture factory replaced the model, so the delivery
is one file and the shop's policy allows that fixture. A real organization
must never allow it. The CI jobs, signatures, policy and gate are real.
:::

:::note[Since the next release]
Values are from the v0.7.1 run. The row names follow `main`:
**Dependencies** (Ingredients in v0.7.1), and the standards validation that
was a separate Standards row now runs inside **Integrity**.
:::

## What it stops, and what it can't

| Attack | Stopped? | By what |
| --- | --- | --- |
| Hand-write or forge a receipt | Yes | No valid GitHub signature; `require_signed` gives exit 1 |
| Sneak an extra change into the PR | Yes | No signature covers that file |
| Weaken a check or contract in the factory | Yes | The definition digest changes, so it is not on `factories.allow` |
| Rewrite the gate or policy in the PR | Yes | The gate runs from the base branch and reads the policy there |
| Replay an old delivery onto a changed main | Yes | Before-images must match the PR base |
| Move the `v0.7.1` tag to other code | Yes | The `workflow_sha` pin |
| A prompt-injected agent writes malicious code | **Only as good as your checks** | It can't sign or skip checks, but code that passes weak checks still passes |
| Compromise GitHub or Sigstore | No | That is the trust root, as for npm provenance |

## Merge everything else the normal way

A human PR fails the gate by design:

```text
[x] Not a factory delivery: no changed file carries an attestation from factories.signers. This check only passes factory deliveries; merge other PRs through your normal review process (e.g. a ruleset bypass for maintainers).
```

Merge it through a ruleset bypass after the usual review, or let factories
deliver to repositories or branches only they write to. APMX is for your own
factories; it does not judge AI pull requests from outside contributors.

## One factory for many projects

A private platform repository runs approved factories against many
repositories and delivers a pull request, a branch or a bundle
(`run.yml`'s `deliver` input). A GitHub App mints a token per job, scoped to
one target, and the agent's job never receives it. An organization ruleset
requires the gate on every repository, and the organization's
`apm-policy.yml` pins the approved factories and the signer. "Every
repository's gate trusts one thing: the platform's runs of a reviewed
`run.yml`." See
[examples/factory-platform](https://github.com/danielmeppiel/apmx/tree/main/examples/factory-platform).
