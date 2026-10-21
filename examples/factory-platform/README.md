# Central factory platform

One platform repository runs approved factories against many repositories
and opens code-only pull requests there. Every repository's gate trusts one
thing: the platform's runs of a reviewed `run.yml`.

```text
                 acme/factory-platform   (platform team)
                 factory.yml -> danielmeppiel/apmx run.yml@<sha>
                 App "acme-factory"  (contents + pull-requests write)
                     |
   target   (App token, read)   fetch acme/shop at its base commit -> artifact
   produce  (model token only)  the agent builds; never sees an App token
   check    (no agent)          re-runs the factory's checks on the outputs
   attest   (OIDC)              signs; attestations stored in factory-platform
   deliver  (App token, write)  pushes apmx/run-<id> to acme/shop, opens the PR
                     |
     +---------------+---------------+------------------+
     v               v               v                  v
  acme/shop     acme/billing     acme/web     ...  every repository
     gate:   org ruleset requires apmx-audit.yml on apmx/* PRs
     policy: org apm-policy.yml -> signers: run.yml@<sha>, repo: factory-platform
```

| File | Where it goes |
| --- | --- |
| [`factory.yml`](factory.yml) | `acme/factory-platform/.github/workflows/`: `workflow_dispatch` with `target-repository` and `request` |
| [`factory-weekly.yml`](factory-weekly.yml) | same place: a schedule that dispatches `factory.yml` per target |
| [`audit.yml`](audit.yml) | `acme/factory-platform/.github/workflows/apmx-audit.yml`: the required gate |
| [`apm-policy.yml`](apm-policy.yml) | the organization policy repository, `acme/.github/apm-policy.yml` |

## Setup

1. **Writer App** (`acme-factory`). Repository permissions: *Contents* read and
   write, *Pull requests* read and write, *Metadata* read. Nothing else: no
   *Workflows* (GitHub then rejects any push that touches `.github/workflows/`;
   deliver refuses those paths anyway) and no *Administration*. Install it on
   the repositories the platform may change. In the platform repository set
   the variable `FACTORY_APP_CLIENT_ID` and the secret `FACTORY_APP_PRIVATE_KEY`.
   `run.yml` mints a token per job scoped to the one target repository:
   *Contents: read* in `target`, *Contents* and *Pull requests: write* in
   `deliver`.
2. **Platform repository.** Private, with only the platform team as writers:
   anyone who can dispatch `factory.yml` or edit it controls what is signed,
   and its run artifacts hold copies of target code. Add `COPILOT_TOKEN`.
3. **Organization policy.** Put [`apm-policy.yml`](apm-policy.yml) in
   `acme/.github`. Pin each approved factory definition digest and the signer:
   `run.yml` at `workflow_sha`, `repo: acme/factory-platform`.
4. **Audit App** (`acme-factory-audit`), read-only: *Attestations* read,
   *Contents* read, installed only on `factory-platform` and `.github`. Share
   `APMX_AUDIT_APP_CLIENT_ID` and `APMX_AUDIT_APP_PRIVATE_KEY` with all
   repositories as organization variable and secret. With a public platform
   repository and policy, `github.token` is enough.
5. **Organization rulesets** (Settings, Rulesets, New branch ruleset):
   - *apmx gate*: target all repositories, the default branch; *Require
     workflows to pass before merging*, workflow
     `acme/factory-platform/.github/workflows/apmx-audit.yml` pinned to a
     commit. No bypass actors. In the platform repository allow access from
     the organization's repositories (Settings, Actions, Access).
   - *apmx branches*: target all repositories, `refs/heads/apmx/**`;
     *Restrict creations*, *Restrict updates* and *Restrict deletions*, with
     only the `acme-factory` App as bypass actor. Only the platform can then
     push the branches the gate treats as factory PRs.

## What a target trusts

- `danielmeppiel/apmx` `run.yml` at the pinned `workflow_sha`, run from
  `acme/factory-platform`: it keeps the agent away from the App tokens, the
  checks of record and the signing identity.
- The platform team: who may dispatch runs and which factories are on
  `factories.allow`.
- The gate itself, which runs from the platform's pinned commit and executes
  no code from the pull request.

Delivered branches live in the target repository, so the target's own
`pull_request` workflows run the delivered code like any branch PR: keep
deployment secrets behind environments.

Fork pull requests to the platform repository never run `factory.yml`
(`workflow_dispatch` and `schedule` only).
