---
title: Approve, upgrade and revoke factories
description: "Day-two policy tasks: approve a factory by its definition digest, roll out in warn mode, re-pin the trusted signer, and revoke a skill."
---

Your repository's `apm-policy.yml` decides which factories and which signer
count. Each section below is one task on its `factories` section. Every key is
in the [policy reference](/apmx/docs/reference/policy/).

## Find the definition digest

A factory is approved by its **definition digest**: one SHA-256 over its
contracts, checks, checker files, skill pins, budgets, limits and
dependencies. Get the full value from any of:

- `apmx audit --explain factory` (the `definition` line);
- `apmx audit --format json`, key `facts.definitionSha256`;
- the `definition-sha256` output of `run.yml`.

The Factory row of `apmx audit` shows it shortened:
`definition sha256:0bec0549b9af..`.

```text title="apmx audit receipt.intoto.jsonl --explain factory (v0.7.1)"
Factory
  definition   sha256:0bec0549b9af1e093c5204610163203ca6a58ea7602ad1021cdd381c042b039e
  package      danielmeppiel/apmx-demo-factory#v1.0.0
  commit       e8e9c5a7b1f11c668860a22d98022591d77cea97
  builder      https://github.com/danielmeppiel/apmx  apm 0.33.0 apmx 0.6.0
  invocation   20261021T184925Z-67361d3ba1d4  2026-10-21T18:49:25.641658+00:00 .. 2026-10-21T18:53:09.817427+00:00
  base         (local)  gitTree dabee76cced986f1784ea768c67307956faa2900
  input        request.md  sha256:1808ed8ffe32..
  (definition bytes not present: evidence/ was not shipped)
```

## Approve a factory

Add `<package>@sha256:<definition digest>` to `factories.allow`:

```yaml title="apm-policy.yml"
factories:
  allow:
    - danielmeppiel/apmx-demo-factory@sha256:0bec0549b9af1e093c5204610163203ca6a58ea7602ad1021cdd381c042b039e
```

The name before `@sha256:` is a label shown in the Factory row; only the
digest is compared. An unpinned pattern such as `danielmeppiel/*` allows
nothing, and the row says so:

```text title="Factory row, allow: [danielmeppiel/*] (v0.7.1 and main, trimmed to the row)"
[x] Factory       definition sha256:0bec0549b9af.. is not on factories.allow
                  policy: policies/allow-unpinned.yml (--policy); 0 pinned definition(s)
                  1 unpinned package pattern(s) allow nothing yet: receipts do not record their factory package; pin <package>@sha256:<definition digest>
                  definition sha256:0bec0549b9af..  danielmeppiel/apmx-demo-factory#v1.0.0  5 contracts, 9 checks
```

Without an `allow` key, any definition passes the Factory row. `allow: []`
allows none.

## Roll out without blocking

Start with `factories.enforcement: warn`. Factory, Revocation and Identity
violations then print `[!]` and the verdict stays VALID, so you see what
would fail before it blocks:

```yaml title="apm-policy.yml"
factories:
  enforcement: warn
  allow:
    - acme/other-factory@sha256:1111111111111111111111111111111111111111111111111111111111111111
```

```text title="Factory row and verdict with enforcement: warn (v0.7.1 and main, trimmed)"
[!] Factory       definition sha256:0bec0549b9af.. is not on factories.allow
                  policy: policies/allow-miss-warn.yml (--policy); 1 pinned definition(s)
                  definition sha256:0bec0549b9af..  danielmeppiel/apmx-demo-factory#v1.0.0  5 contracts, 9 checks
...
[+] VALID   (content-bound; not authenticated)
```

When the rows look right, remove the key or set `enforcement: block` (the
default).

## Upgrade a factory

Any change to contracts, checks, checker files, skill pins, budgets, limits
or dependencies gives a new definition digest. Harness, model and the package
name do not.

1. Review the new factory version.
2. Add its digest to `factories.allow`, next to the old one.
3. Remove the old digest once nothing you still accept was built with it.

## Upgrade the trusted signer

The signer entry pins APMX's `run.yml` by commit:

```yaml title="apm-policy.yml"
factories:
  signers:
    - workflow: danielmeppiel/apmx/.github/workflows/run.yml
      workflow_sha: a70c1939dddc6f5d7888b76fd5bb8bab3e69e886   # git rev-parse v0.7.1^{commit}
      repo: acme/factories   # where the factory ran: its attestations are stored there
```

When your factories move to a new APMX release:

1. Review the new `run.yml`.
2. In a clone of `danielmeppiel/apmx`, run `git rev-parse <tag>^{commit}` and
   put the result in `workflow_sha`. The annotated tag object's SHA is also
   accepted.
3. While both releases still deliver, list two signer entries, one per
   commit, and remove the old one when its deliveries are merged.

A delivery signed at another commit fails Identity with
`signed by <workflow> at <commit>, policy requires <workflow_sha>`. A signer
without `workflow_sha` trusts every run of that workflow file: Identity still
passes, with the note `signer not pinned (any ref); pin workflow_sha` (or
`signer pinned by mutable ref <ref>; pin workflow_sha` when the entry ends in
`@<ref>`).

## Revoke a skill

To stop accepting deliveries built with a specific skill, add its exact
`purl` and `bodySha256` to `factories.revoked`. Both must match.

Find the `purl` with `apmx audit --explain dependencies`, on the
`capability` line. `bodySha256` is the SHA-256 of the skill's `SKILL.md`; the
explain line shows its first 12 hex digits.

```text title="apmx audit receipt.intoto.jsonl --explain dependencies (main, unreleased)"
Dependencies
  capability   python-testing-patterns  pkg:generic/agents@46891e7e60da0e52baf1050b7b6391b64e84c6d9  body sha256:a1020718f316..
  component    wshobson/agents  pkg:generic/agents@46891e7e60da0e52baf1050b7b6391b64e84c6d9
  component    danielmeppiel/apmx-demo-factory  pkg:generic/apmx-demo-factory@e8e9c5a7b1f11c668860a22d98022591d77cea97
  retained     apm.lock.yaml  sha256:2aa9ff56bb6d..
  retained     apm.yml  sha256:5c70a5edec95..
  retained     imports-apm.yml  sha256:b37d01c6ae86..
```

:::note[Since the next release]
The topic is `dependencies`. In v0.7.1 it is `apmx audit --explain ingredients`
and the section is headed `Ingredients`; each version rejects the other
spelling.
:::

```yaml title="apm-policy.yml"
factories:
  revoked:
    - purl: pkg:generic/agents@46891e7e60da0e52baf1050b7b6391b64e84c6d9
      bodySha256: a1020718f31629c2a5b20731cad1804dbb81b18800495c5cebdf16e6cac9c175
```

```text title="Revocation row with that policy (v0.7.1 and main, trimmed to the row)"
[x] Revocation    capability python-testing-patterns is revoked (factories.revoked)
                  purl=pkg:generic/agents@46891e7e60da0e52baf1050b7b6391b64e84c6d9  bodySha256=a1020718f31629c2a5b20731cad1804dbb81b18800495c5cebdf16e6cac9c175
```

Then fix the pin in the factory's `apm.yml`, run it again, and approve the
new definition digest.

## Share one policy across repositories

Without a repository `apm-policy.yml`, `apmx audit` uses the organization
policy APM discovers from the `origin` remote (in a `.github-private`,
`.github`, `.apm` or `_apm` repository). A policy can also `extends:` another,
up to 5 levels, and the merge only tightens:

| Key | Merged across levels |
| --- | --- |
| `allow` | intersection of the levels that set it |
| `signers` | intersection; a child may pin a signer its parent lists unpinned, never unpin one |
| `require_signed` | true if any level says true |
| `revoked` | union |
| `enforcement` | `warn` only if every level that sets it says `warn` |

`extends` is not followed with `--offline`; the Policy row then warns that the
policy is partial.

## Test a policy before you commit it

Audit a receipt against the file you are editing:

```sh
apmx audit path/to/receipt.intoto.jsonl --policy ./apm-policy.yml
```

A mistake fails closed. A misspelled key makes the whole policy unloadable,
and the audit is INCOMPLETE (exit 2), never a pass:

```text title="Policy row with factories.require_sigend (v0.7.1 and main, trimmed to the row)"
[!] Policy        policies/typo.yml (--policy) could not be loaded: policies/typo.yml (--policy): unknown key factories.require_sigend (expected allow, require_signed, signers, revoked, enforcement)
                  A policy that cannot be loaded is never treated as a pass.
```

Next: what each key protects, in the
[trust model](/apmx/docs/trust-model/#what-it-stops-and-under-which-policy).
