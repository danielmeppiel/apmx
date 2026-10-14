# Accept factory PRs by proof

Review the factory once, then accept its pull requests by proof instead of
re-reading every generated diff. This is the whole receiving side: one policy
section and one workflow step.

1. **Produce in CI.** The factory repository (here `acme/factories`) calls
   [`run.yml`](../../.github/workflows/run.yml): the agent runs in one job,
   `apmx check` reruns the checks in a fresh job without the agent, and a third
   job signs the receipt with the workflow's OIDC identity. Commit the
   `apmx-delivery` artifact (outputs plus `.apm/.../receipt`) as a pull request
   to the receiving repository.
2. **Pin what you accept.** Copy [`apm-policy.yml`](apm-policy.yml) to the
   receiving repository's root: the approved factory definition digest, the
   signer workflow, and any revoked capability content.
3. **Gate the PR.** Add [`audit.yml`](audit.yml). `apmx audit` finds the
   receipt the PR adds, binds the PR's change to its outputs, reads the policy
   from the PR base, and verifies the signature. Exit 0 is the pass.

An excerpt of the gate's output:

```text
[+] Policy        apm-policy.yml (repository, PR base 1a2b3c4d5e6f)   factories enforcement block
[+] Factory       definition sha256:0311c27f3d7b.. on factories.allow (acme/checkout-factory)
[+] Revocation    0 of 1 capability revoked; factories.revoked is empty
[+] Outputs       PR #42: 3/3 outputs match at head 9f8e7d6c5b4a; 4 changed files bound
[+] Identity      signed by danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v1
                  checks: separate job

[+] VALID   (authenticated: danielmeppiel/apmx/.github/workflows/run.yml)
```

A PR that also edits anything else, changes `apm-policy.yml`, carries an
unsigned or differently-signed receipt, or uses a factory definition you did
not approve fails with exit 1. A receipt or policy that cannot be read is exit
2, never a pass. See [docs/audit.md](../../docs/audit.md).

APM 0.33.0 warns `Unknown top-level policy key: 'factories'` when it loads
this policy. That is expected: the section belongs to apmx.
