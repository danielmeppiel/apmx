---
title: Read the receipt
description: One in-toto bundle per run (SLSA provenance, SBOM, check results, VSA), bound to the exact files it describes. What it records, how to verify it, and what it does not guarantee.
---

Every COMPLETE run writes a **receipt** next to its record: one in-toto
bundle bound to the delivered files. It keeps the **Traceability** promise:
every delivered file is cryptographically bound to the factory that made it
and the checks it passed. It is evidence of where a change came from and what
it passed, not a guarantee that the code is correct. Think of a food label:
what went in, how it was built, what was checked. It has three parts:

| Part | What it is | Who needs it |
| --- | --- | --- |
| `receipt.intoto.jsonl` | One standard in-toto bundle: one DSSE envelope per line, each an in-toto Statement v1 | everyone; `apmx audit` |
| `outputs/` | The delivered files, at their project paths | the user, the PR |
| `evidence/sha256/<hex>` | Content-addressed material the statements name by digest | forensics and replay; optional for verification |

REJECTED and HALTED runs get no receipt.

## One line, one claim, all about the same files

| Line | Standard | Says |
| --- | --- | --- |
| 1 | SLSA Provenance v1 | built by this factory, from these inputs, by this harness and model |
| 2..n | in-toto Test Result v0.1, one per check | check X ran on these files: PASSED |
| last | CycloneDX 1.5 SBOM (the agent bill of materials) | the APM packages and skills the agents were given, from the APM lockfile |
| (signer) | SLSA Verification Summary v1, signed | the verifier checked every other line: PASSED |

The last statement comes from a **trusted signer**, a job the agent cannot
reach, which your policy pins (`factories.signers`). See
[which signer v0.7.1 uses](../accept-prs/#1-run-agent-checks-and-signer-apart).

**Every statement names the same subjects: the delivered files** (for a
factory that writes patches, the files the patches produce, not the `.diff`).

```json title="Line 1, shortened (from the checkout factory)"
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "src/pricing.py", "digest": {"sha256": "e6981e53e1ed..."}}, "..."],
 "predicateType": "https://slsa.dev/provenance/v1",
 "predicate": {
  "buildDefinition": {
   "buildType": "https://github.com/danielmeppiel/apmx/build/factory/v2",
   "externalParameters": {"factory": {"package": "danielmeppiel/apmx-demo-factory#v1.0.0",
     "commit": "e8e9c5a7b1f1...", "definition": {"sha256": "0bec0549b9af..."}}}}}}
```

```json title="A check line, shortened"
{"predicateType": "https://in-toto.io/attestation/test-result/v0.1",
 "predicate": {"result": "PASSED",
  "configuration": [{"name": "checks/acceptance.py", "digest": {"sha256": "8a94940e06d8..."},
    "annotations": {"check": "shipping-examples", "stage": "build",
      "command": "python3 -I -B checks/acceptance.py changes.diff",
      "scope": "Observed checks over the reconstructed candidate files; not a correctness guarantee."}}]}}
```

## Your supply chain, answered by fields

| Question | Where the receipt answers it |
| --- | --- |
| What went in? | The CycloneDX line lists every APM package and skill, as pinned by the APM lockfile. Provenance `resolvedDependencies` carries the inputs, `apm.yml` and `apm.lock.yaml`. |
| Which factory state built it? | `externalParameters.factory.definition`: the SHA-256 of the exact contracts, checks, checker files, skill pins and budgets. |
| Which harness and model? | `internalParameters.stages[]`, as each harness reported them. Recorded, not enforced. |
| Which checks passed, on what? | One test-result line per check, naming the checker file by digest. |
| Can one component be fixed? | Change one pin, run again, approve the new definition digest, and revoke the old content with `factories.revoked`. |

## Verify it

```text title="apmx audit (local, unsigned)"
$ apmx audit
Receipt   .apm/chains/20261021T184925Z-67361d3ba1d4/receipt.intoto.jsonl   (newest COMPLETE run)   5 contracts   unsigned

[i] Policy        none found
[+] Integrity     11 statements intact and well-formed; line digests computed; evidence 31/31 blobs verified
[+] Factory       definition sha256:0bec0549b9af..  danielmeppiel/apmx-demo-factory#v1.0.0  5 contracts, 9 checks
[+] Checks        9/9 passed (in-toto test-result)
[+] Dependencies  2 APM dependencies   apm audit --ci: clean (content, lock, drift)
[i] Revocation    no policy: nothing revoked to check
[+] Outputs       4/4 delivered files match receipt subjects (re-hashed)
                  4 files bound: docs/checkout.md src/checkout.py src/pricing.py tests/test_free_shipping.py
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

:::note[Since the next release]
This output uses the audit rows on `main`: **Dependencies** (named
Ingredients in v0.7.1) and **Integrity**, which now also validates the
standards (a separate Standards row in v0.7.1). The rows are Policy,
Integrity, Factory, Checks, Dependencies, Revocation, Outputs, Identity.
:::

`apmx audit --explain` prints what the receipt records, and
`apmx audit --export abom` writes the CycloneDX inventory as a standalone
file for SBOM tools. The verifier can also run without importing APMX:
see [docs/evidence.md](https://github.com/danielmeppiel/apmx/blob/main/docs/evidence.md#independent-offline-verification).

## What it does not guarantee

:::caution
Local receipts are **unsigned, same-user observations**. They provide
inspectability and content binding, not an authenticated signer, an SLSA
level, a sandbox, an independent witness, general software correctness or
permission to merge or deploy. A summary signed by a trusted signer your
policy allows is what says who produced it.
:::

`evidence/` holds transcripts, inputs and the files the run started from.
They can contain private code or secrets: **review before sharing**, and never
put `evidence/` in a pull request.

Next: [auto-merge factory PRs on trusted evidence](../accept-prs/).
