# Accept factory PRs by proof

Review the factory once, then accept its pull requests by proof instead of
re-reading every generated diff. This is the whole receiving side: one policy
section and one workflow step.

1. **Produce in CI.** The factory repository (here `acme/factories`) calls
   [`run.yml`](../../.github/workflows/run.yml)
   (`danielmeppiel/apmx/.github/workflows/run.yml@v0.7.0`): the agent runs in one job,
   `apmx check` reruns the checks in a fresh job without the agent, and the
   attest job signs a SLSA Verification Summary over the delivered files, and
   the receipt bundle it binds, with the workflow's OIDC identity. Open a pull
   request to the receiving repository from the `apmx-delivery` artifact: it
   holds **code only**, the delivered files at their repository paths (for a
   factory that writes patches, the patched files themselves), plus an
   `.apmx-deleted` list of files to delete. Never commit a receipt.
2. **Pin what you accept.** Copy [`apm-policy.yml`](apm-policy.yml) to the
   receiving repository's root: the approved factory definition digest, the
   signer workflow at its commit, and any revoked capability content.
3. **Gate the PR.** Add [`audit.yml`](audit.yml). `apmx audit` looks the PR's
   changed files up by SHA-256 in the signer's attestation store, requires
   every one to be a subject of one PASSED verification summary from the
   allowed signer, checks the receipt statements it binds against the policy
   read from the PR base, and refuses a PR built on a stale base. Exit 0 is
   the pass.

An excerpt of the gate's output:

```text
Receipt   attestations for PR #42   (looked up by file digest)   5 contracts   signed

[+] Policy        apm-policy.yml (repository, PR base 1a2b3c4d5e6f)   factories enforcement block
[+] Integrity     12 statements; line digests computed; the VSA binds every other line; evidence not present (optional)
[+] Factory       definition sha256:0bec0549b9af.. on factories.allow (acme/checkout-factory)
[+] Revocation    0 of 1 capability revoked; factories.revoked is empty
[+] Outputs       PR #42: 4 changed files = delivered files at head 9f8e7d6c5b4a; before-images match base 1a2b3c4d5e6f
[+] Identity      signed by danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.7.0 at 315491bff45f (tag object 86978e639fcf)

[+] VALID   (authenticated: danielmeppiel/apmx/.github/workflows/run.yml)
```

A PR that also edits anything else, changes `apm-policy.yml`, was not signed
by the allowed signer at its commit, was built on a base that has since
changed the same files, or uses a factory definition you did not approve fails
with exit 1. A policy or attestation store that cannot be read is exit 2,
never a pass. See [docs/audit.md](../../docs/audit.md).

APM 0.33.0 warns `Unknown top-level policy key: 'factories'` when it loads
this policy. That is expected: the section belongs to apmx.
