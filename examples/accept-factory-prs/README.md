# Accept factory PRs by proof

Review the factory once, then accept its pull requests by proof instead of
re-reading every generated diff. This is the whole receiving side: one policy
section and one workflow step.

1. **Produce in CI.** The factory repository (here `acme/factories`) calls
   [`run.yml`](../../.github/workflows/run.yml)
   (`danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1`): the agent runs in one job,
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
3. **Gate the PR.** Add [`audit.yml`](audit.yml) and make its `audit` check
   required, with GitHub Actions as the expected source. It runs on
   `pull_request_target`, so the base branch's copy runs and a PR cannot edit
   or delete its own gate; it checks the PR head out as bytes, without
   credentials, and executes nothing from it. `apmx audit` looks the PR's
   changed files up by SHA-256 in the signer's attestation store, requires
   every one to be a subject of one PASSED verification summary from the
   allowed signer, checks the receipt statements it binds against the policy
   read from the PR base, and refuses a PR built on a stale base. Exit 0 is
   the pass.
4. **Merge everything else the normal way.** Green means accepted by proof
   and nothing else, so auto-merge on the check is safe. A human PR fails it
   with `Not a factory delivery: no changed file carries an attestation from
   factories.signers`; maintainers merge it through a ruleset bypass after
   their usual review, or factories deliver to a repository or branch only
   they write to.

An excerpt of the gate's output:

```text
Receipt   attestations for PR #42   (looked up by file digest)   5 contracts   signed

[+] Policy        apm-policy.yml (repository, PR base 1a2b3c4d5e6f)   factories enforcement block
[+] Integrity     12 statements; line digests computed; the VSA binds every other line; evidence not present (optional)
[+] Factory       definition sha256:0bec0549b9af.. on factories.allow (acme/checkout-factory)
[+] Revocation    0 of 1 capability revoked; factories.revoked is empty
[+] Outputs       PR #42: 4 changed files = delivered files at head 9f8e7d6c5b4a; before-images match base 1a2b3c4d5e6f
[+] Identity      signed by danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.7.1 at a70c1939dddc (tag object 917ffb4e5406)

[+] VALID   (authenticated: danielmeppiel/apmx/.github/workflows/run.yml)
```

A factory PR that also edits anything else, changes `apm-policy.yml`, was not signed
by the allowed signer at its commit, was built on a base that has since
changed the same files, or uses a factory definition you did not approve fails
with exit 1. A policy or attestation store that cannot be read is exit 2,
never a pass. See [docs/audit.md](../../docs/audit.md#a-pr-cannot-rewrite-its-own-gate)
for why the gate must run on `pull_request_target`, and for organizations, an
organization ruleset that requires the gate workflow from a central
repository ([`../factory-platform`](../factory-platform)).

APM 0.33.0 warns `Unknown top-level policy key: 'factories'` when it loads
this policy. That is expected: the section belongs to apmx.
