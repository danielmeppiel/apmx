# Accept factory PRs on trusted evidence

Review the factory once, then merge its pull requests on trusted evidence
instead of re-reading every generated diff. This is the whole receiving side: one policy
section and one workflow step.

1. **Produce in CI.** The factory repository (here `acme/factories`) calls
   [`run.yml`](../../.github/workflows/run.yml)
   (`danielmeppiel/apmx/.github/workflows/run.yml@v0.7.1`): the agent runs in one job,
   `apmx check` reruns the checks in a fresh job without the agent, and a
   trusted signer, the attest job, signs a SLSA Verification Summary over the
   delivered files, and the receipt bundle it binds, with the workflow's
   GitHub Actions OIDC identity through Sigstore. Open a pull
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
4. **Merge everything else the normal way.** Green means accepted on trusted
   evidence and nothing else, so auto-merge on this check merges only factory
   deliveries that verify against your policy; it is not a guarantee that the
   code is correct. A human PR fails it
   with `Not a factory delivery: no changed file carries an attestation from
   factories.signers`; maintainers merge it through a ruleset bypass after
   their usual review, or factories deliver to a repository or branch only
   they write to.

The gate's output, verbatim, on a real factory PR
([danielmeppiel/apmx-demo-shop#11](https://github.com/danielmeppiel/apmx-demo-shop/pull/11)),
audited with apmx 0.7.1:

```text
$ apmx audit
Receipt   attestations for PR #11   (looked up by file digest)   1 contract   signed

[+] Policy        apm-policy.yml (repository, PR base 4fed7ee21e1d)   factories enforcement block
[+] Integrity     4 statements; line digests computed; the VSA binds every other line; evidence not present (optional)
[+] Standards     in-toto Statement v1, SLSA Provenance v1, in-toto Test Result, CycloneDX 1.5, SLSA VSA v1: schema-valid
[+] Factory       definition sha256:c011c35b5790.. on factories.allow (danielmeppiel/apmx/tests/fixtures/run-workflow-package)
                  definition sha256:c011c35b5790..  danielmeppiel/apmx/tests/fixtures/run-workflow-package#v0.7.1  1 contract, 1 check
[+] Checks        1/1 passed (in-toto test-result)
[+] Ingredients   1 APM dependency   apm audit --ci: clean (content, lock, drift)
                  installed bytes match CycloneDX inventory and recorded hashes
[+] Revocation    0 of 0 capabilities revoked; factories.revoked is empty
[+] Outputs       PR #11: 1 changed file = delivered files at head d85c418bdf11; before-images match base 4fed7ee21e1d
                  built on 2ca997db3993, in the PR base's history
                  1 file bound: handoff.json
[+] Identity      signed by danielmeppiel/apmx/.github/workflows/run.yml@refs/tags/v0.7.1 at a70c1939dddc (tag object 917ffb4e5406)
                  run https://github.com/danielmeppiel/apmx-demo-shop/actions/runs/39740103826/attempts/1

[+] VALID   (authenticated: danielmeppiel/apmx/.github/workflows/run.yml)
```

Since the next release, Standards is part of the Integrity row and
Ingredients is named Dependencies.

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
