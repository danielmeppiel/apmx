# Auditing a receipt: `apmx audit`

`apmx audit` answers one question. Someone handed you outputs and a receipt:
can you trust that this factory, with these ingredients, produced these files,
and that its checks passed?

```text
$ apmx audit factory/.apm/chains/20261009T090958Z-7a55db0b0bb0/receipt
Receipt   factory/.apm/chains/20261009T090958Z-7a55db0b0bb0/receipt   2 contracts   unsigned

[+] Integrity     all 26 files match index.json (SHA-256)
[+] Standards     in-toto Statement v1, SLSA Provenance v1, CycloneDX 1.5: schema-valid
[+] Factory       definition sha256:0311c27f3d7b..  2 contracts, 2 checks
[+] Checks        2/2 passed (in-toto test-result)
[+] Ingredients   0 APM dependencies   nothing to audit
[+] Outputs       3 files bound: final.json first.json second.json
[!] Identity      unsigned: proves content binding, not who ran it

[+] VALID   (content-bound; not authenticated)
```

Every COMPLETE run ends by printing this exact command on its `Next` line, and
delivers a [receipt](evidence.md) next to its record:
`.apm/runs/<id>/receipt`, `.apm/chains/<id>/receipt` or
`.apm/controllers/<id>/receipt`. REJECTED and HALTED runs have no receipt.

## Command

```text
apmx audit RECEIPT_DIR [--outputs DIR] [--policy SOURCE] [--offline] [--format text|json]
```

| Option | Meaning |
| --- | --- |
| `--outputs DIR` | Re-hash the delivered files in `DIR`, for example a PR checkout, against the receipt's bound output subjects. Without it, outputs are checked only inside the receipt. |
| `--policy SOURCE` | Passed to `apm audit --policy`: `org`, `owner/repo`, an `https://` URL or a local file. A relative file is resolved from your current directory. |
| `--offline` | Do not launch APM. The Ingredients row says `not audited (offline)`; this is a warning, not a failure. It cannot be combined with `--policy`. |
| `--format json` | Machine output for CI: `{"schema": "apmx-audit/1", "result", "exitCode", "reason", "rows", "facts", "signed": false}`. |

`audit` is a reserved word. `apmx <factory-dir | x.contract.md | package-ref>`
is still the run shorthand. To run a local directory named `audit`, write
`./audit`.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | VALID: every row passed. An `--offline` ingredients warning still counts as valid. |
| 1 | INVALID: a structural row failed, `--outputs` differ, reinstalled ingredients differ from the receipt, or `apm audit --ci` (including its policy) reported a failing check. |
| 2 | Usage error or missing receipt, **or the ingredients could not be audited**. This covers network or authentication failures, unreachable packages, and a `--policy` that APM could not load. Exit 2 is never a pass. Rerun with access, or use `--offline` to skip the ingredients explicitly. |

The final line is `VALID`, `INVALID <first reason>` or `INCOMPLETE <reason>`.
Output is printable ASCII and uses the `[+]`, `[x]`, `[!]` and `[i]` symbols.
Colour appears only on a terminal, and never when `NO_COLOR` is set.

## Rows

Structural rows run in order and stop at the first failure. Later rows show
`[i] not evaluated`, and APM is never launched for a receipt that is already
invalid.

| Row | What it checks |
| --- | --- |
| Integrity | Every file matches `index.json` (SHA-256 and size). There are no unindexed files or symlinks, and every descriptor resolves inside the receipt. |
| Standards | Every statement is an in-toto Statement v1, validated with the upstream in-toto protobuf bindings. Production predicates are SLSA Provenance v1 and check predicates are in-toto Test Result. `abom.cdx.json` is valid against the pinned CycloneDX 1.5 JSON Schema. |
| Factory | The `definition.json` digest; producer statements agree with the definition (build type, builder, contract digest, checks, outputs, checker resources, capability bodies); capability bindings agree with the CycloneDX inventory. |
| Checks | Every recorded check invocation has exactly one test-result statement with consistent configuration, subjects and reports. Every check of a completed attempt passed. |
| Ingredients | Dependency trust, delegated to APM (see below). |
| Outputs | The aggregate and per-attempt production subjects bind exactly the delivered artifacts. With `--outputs`, the delivered files are also re-hashed. |
| Identity | Always `[!] unsigned`: receipts prove content binding, not who ran it. |

The CycloneDX schemas (`bom-1.5`, `spdx`, `jsf-0.82`, pinned by SHA-256 at the
upstream commit noted in `NOTICE`) ship inside apmx. Structural audit
therefore works offline.

## Ingredients: APM owns dependency trust

apmx does not reimplement dependency auditing. The receipt retains the run's
exact `apm.yml` and `apm.lock.yaml`. For an audit, apmx:

1. Checks that the retained lock projects to the definition's
   `resolvedDependencies`. With zero dependencies, the row reads
   `0 APM dependencies` and nothing is launched.
2. Stages the manifest and lock in a fresh temporary project and runs the
   **bundled, pinned** APM: `apm install --frozen --only apm --target
   agent-skills --no-trust-bin --no-policy`. Scripts are disabled
   (`APM_NO_SCRIPTS=1`); no receipt code runs.
3. Confirms that the reinstall recorded the same content hashes as the
   receipt's lock. For local packages, APM 0.30.0 rewrites lock hashes from
   whatever it finds, so apmx compares them and then restores the retained
   lock bytes. It also checks that every selected skill body matches the
   receipt's `bodySha256`, and that the pinned APM derives exactly the
   receipt's CycloneDX components from the retained lock.
4. Runs `apm audit --ci --no-fail-fast` against the retained lock. This covers
   hidden-Unicode content scanning, lock consistency, install-replay drift
   and, with `--policy`, org policy.
5. Deletes the temporary project.

**Policy decision.** Without `--policy`, the audit runs with `--no-policy`, so
APM's org auto-discovery never silently applies a policy you did not choose.
With `--policy`, apmx requires evidence that APM actually enforced it. APM
skips enforcement with only a warning when a policy cannot be fetched, so apmx
reports such a run as `INCOMPLETE` (exit 2), never as a pass. `--policy org`
needs a git remote to discover the organization. The temporary audit project
has none, so pass `owner/repo`, a URL or a file instead.

Local-path dependencies resolve on the auditor's machine at the absolute
paths recorded in the lock. When those paths do not exist there, the
ingredients are unavailable (exit 2). Remote dependencies need network and
package access with your normal APM authentication.

## What VALID does not mean

The receipt is **unsigned**. VALID means the receipt is internally consistent
and that its files are bound by content to this factory definition, these
ingredients and these check results. It does not authenticate who ran the
factory. Anyone who can rewrite every file and hash can forge a
self-consistent receipt. Signing, authenticated builder identity and an
approved-factory allowlist are out of scope here; they follow the trusted
receiver work. VALID is not a sandbox, a correctness guarantee or permission
to merge or deploy.

## Independent verification

`scripts/verify_evidence.py` is a thin wrapper over the same verifier
(`src/apmx/audit/verifier.py`). It loads the verifier by file path, so it
validates a receipt **without importing APMX**; see
[evidence verification](evidence.md#independent-offline-verification).
