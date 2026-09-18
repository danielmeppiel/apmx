# Execution results and record migration

The 0.4.1 source separates **execution** from **assurance**. The contract
format (`needs`, `produces`, `verify`) and consent flags are unchanged.

| Execution outcome | Exit | Meaning |
| --- | --- | --- |
| COMPLETE | 0 | All declared outputs are retained, every exact required check passed, producer/check cleanup was observed, and complete evidence and the final record were validated and persisted. |
| REJECTED | 20 | At least one required check failed. |
| UNPROVEN | 21 | Incomplete output/checks, unavailable consent or policy, or an unauthorized native handoff. Never treat this as success. |
| HALTED | 22 | Execution, cancellation, watchdog, capture, inconsistent evidence or finalization stopped the invocation. |

Preview also exits 0, but performs no execution and issues no completion
record. Check labels are PASS, FAIL and INCOMPLETE; raw process observations
remain in the record.

COMPLETE is not VERIFIED, trust, certification, correct software, a sandbox,
or permission to merge/deploy. Native agents and checks use host files,
network and available logins; model usage can cost money. Run only trusted
contracts. The disclosure appears once before consent/action, including
noninteractive flag mode. A successful factory shows Contract / Checks /
Evidence, counts every required check and output, and prints copyable paths.

## Handoffs remain fail-closed

The strict `VERIFIED-only` policy name is retained for compatibility, but
current native execution cannot establish its isolation assurance. Numeric
0 / COMPLETE **does not pass that gate**. Automation still needs explicit
`--allow-host-access` and `--allow-unproven-inputs` for native factory handoffs.
The interactive factory confirmation explicitly authorizes the same local
profile. Neither path permits missing/extra/duplicate checks, partial
deliveries, changed retained bytes or unconfirmed producer/check completion.
There is no new trust flag or implicit policy relaxation.

`contracts/records.py` remains the single authority. It validates persisted
provisional observations against the admitted plan (including exact ordered
check identities, byte digests, provenance, output inventory, transcript,
producer completion and consent), writes COMPLETE durably, then validates
that final record before returning it. Finalization failures cannot produce a
successful CLI exit. Factory completion follows leaf/handoff validation,
retained aggregate capture and durable aggregate/transcript finalization.
Before the CLI announces success, it also rechecks the exact retained record
identities and evidence after import/package preparation cleanup. This uses
retained copies, not temporary package paths that cleanup may remove. A factory
must match its ordered graph nodes to distinct finalized child records; successful
leaf headlines are not printed ahead of shared preparation cleanup, even in
verbose mode. This is an in-process lifecycle check, not protection against
arbitrary concurrent modification by the same user.

## Record versions

New leaf records use `apm-contract-run/0.3` for both scalar and multiple
outputs. Scalar artifact objects and multi-output `{files, sha256}` inventories
remain distinct; consumers must inspect that shape. New factory records use
`apmx-contract-chain/0.2`. Both retain `result.outcome` and add an `execution`
object (`name`, `exit_code`) and independent `assurance`:

```json
{
  "profile": "native-advisory",
  "isolation": "unavailable",
  "certification": "unproven"
}
```

Leaf `complete: true` still means the record finished finalizing, not that
execution succeeded: rejected and incomplete results also have finalized
records. Require the versioned execution result and validate its evidence.
Factory `complete` means all selected contracts completed. Never infer trust
from either boolean, a raw numeric exit, or an external producer's stdout.
If exact final validation fails, the leaf records `complete: false`,
`phase: finalization_failed` and an actionable `validation_error` or
`finalization_error`, then refuses
the invocation without a success announcement. Missing checks remain UNPROVEN;
inconsistent evidence is HALTED. Aggregate validation/finalization failures
likewise retain an incomplete HALTED record when storage remains writable.
If cleanup encounters malformed or unknown-version records, APMX refuses to
overwrite them and reports both the original failure and unconfirmed stop
persistence. Do not rely on a visible completion record in that case.

Historical `apm-contract-run/0.1` and `/0.2` leaf records and
`apmx-contract-chain/0.1` records remain inspectable as ordinary retained
JSON, transcripts and artifacts. Their bytes, hashes and recorded meanings
are **not migrated or rewritten**. New handoff validation rejects old and
unknown leaf schemas with `record_version` and instructions to rerun with
current source. Unknown outcomes or inconsistent result fields fail closed.
Keep historical evidence; rerun the original contract in a fresh attempt
for new-schema receipts. Do not rename old UNPROVEN/21 or VERIFIED/0 records.

## Current release versus historical downloads

The v0.4.1 source and fresh native release report COMPLETE/0 for successful
native execution after exact evidence finalization. The immutable v0.4.0 tag
records the failed release-preparation candidate; no v0.4.0 release or native
assets were published. Historical v0.3.2 native
binaries and the immutable `be9c5be` source/demo still report UNPROVEN/21. The
installation guide's pinned fallback reproduces that historical behavior, not
the redesign. No v0.3.2 asset, manifest or historical media is rebuilt or
reused by v0.4.1. Match every adapter to its record version; current smoke
fixtures target v0.4.1, not old binaries. For scripts running v0.4.1, replace
old exit-21 completion special cases with exit 0 success handling; do not apply
that migration to v0.3.2 binaries.
