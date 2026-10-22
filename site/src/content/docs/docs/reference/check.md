---
title: apmx check
description: Rerun a factory's own checks on a producer's recorded outputs, with no agent, and write the receipt of record.
---

## Synopsis

```text
apmx check [OPTIONS] PRODUCER_RECEIPT [FACTORY]
apmx check PRODUCER_RECEIPT --from PACKAGE_REF [ENTRY]
```

Reruns a factory's checks, from your checkout, on the outputs another run
recorded, and writes a new receipt: the **receipt of record**. No agent runs.

<details>
<summary>Full <code>apmx check --help</code> (v0.7.1, identical on main)</summary>

```text
Usage: apmx check [OPTIONS] PRODUCER_RECEIPT [FACTORY]

  Re-check a producer's outputs on this machine and write the receipt of
  record.

  PRODUCER_RECEIPT is the receipt.intoto.jsonl (or its run directory, with
  evidence/) of a run made elsewhere, for example by an agent job in CI. Only
  its output bytes are used, matched to contracts by SHA-256; its checks,
  records and claims are ignored, except the harness and models it reports,
  which are recorded as reported by the producer. FACTORY (default: the
  current directory) is run from this checkout with the replay producer: no
  agent runs, the factory's own checks run on the replayed outputs, and a new
  receipt is written under FACTORY/.apm/. The new receipt must have the same
  factory definition digest as the producer's.

  With --from PACKAGE_REF, the factory is that APM package (FACTORY then
  selects an optional package-relative entry) run against the current
  directory, as in `apmx --from PACKAGE_REF`; the receipt is written under
  ./.apm/. The definition digest covers the package's resolved commit and
  content, so a different package, ref or commit withdraws the receipt.

  Checks run with host access, as in `apmx FACTORY --allow-host-access`. Exit
  codes are those of a factory run; 1 also means the producer receipt is
  invalid or its definition differs.

Options:
  --from PACKAGE_REF  Check an APM source package's outputs against the
                      current directory.
  -v, --verbose       Stream native debug logs.
  -h, --help          Show this message and exit.
```

</details>

## Why it exists

The run that produced the work (the "producer", for example the agent's job
in CI) also ran checks and wrote a receipt, but nothing it claims is trusted.
`apmx check` takes only its output bytes, reruns the factory's own checks on
them from your checkout, and records the result. It is what `run.yml`'s
`check` job runs.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `PRODUCER_RECEIPT` | Required. The producer's `receipt.intoto.jsonl`, or its run directory, **with `evidence/`**: the output bytes are read from there. Without `evidence/`, exit 1. Missing argument: usage error, exit 2. |
| `FACTORY` | Default `.`. The local factory directory to run from this checkout; the new receipt is written under `FACTORY/.apm/`. With `--from`, it selects an optional package-relative entry instead. |
| `--from PACKAGE_REF` | The factory is that APM package, run against the current directory; the receipt is written under `./.apm/`. |
| `-v`, `--verbose` | Stream debug logs. |
| `-h`, `--help` | Show help. |

## What it does

1. Verifies the producer bundle's structure. An invalid one stops with exit 1:
   `The producer receipt is invalid (<row>: <msg>).`
2. Extracts each stage's outputs from `evidence/`, matched to contracts by
   SHA-256.
3. Runs the factory with the replay harness
   (`--on replay --allow-host-access --allow-unproven-inputs`): no agent, the
   factory's own checks run on the replayed bytes.
4. Requires the new receipt's factory definition digest to equal the
   producer's. Otherwise it deletes the new receipt and exits 1. The digest
   covers the package's resolved commit and content, so a different package,
   ref or commit withdraws the receipt.

## What carries over

The producer's checks, records and claims are ignored. Only the harness,
harness version, requested model and observed models are carried over, marked
`reportedBy: "producer"` and `checkedBy: "apmx check (replay)"`. The
producer bundle's digest is recorded as a `producer-receipt` input.

## Output

A real re-check of a CI producer receipt:

```text title="apmx check (v0.7.1; identical on main apart from ids and times)"
$ apmx check ../art/apmx-producer-receipt --from danielmeppiel/apmx/tests/fixtures/run-workflow-package#v0.7.1
[i] Replaying 1 recorded output file from ../art/apmx-producer-receipt (definition sha256:c011c35b5790..); no agent runs here.
  [>] Installing packages with APM 0.33.0
    Temporary workspace; your project files are unchanged.
  APM > [*] Validating 1 package...
  APM > [>] Resolving apmx-run-workflow-package...
  APM > [*] Installed 1 APM dependency in 0.8s.
  [+] Packages ready.
[i] Not sandboxed: agents and checks can use host files, network and logins.
    Packages may be installed; model usage may cost money. Run only contracts you trust.

Factory  danielmeppiel/apmx/tests/fixtures/run-workflow-package#v0.7.1   1 contract   replay / default model

[1/1] handoff   needs notes.md -> produces handoff.json
      attempt 1/1  agent 0.2s   checks: [+] identity
      [+] handoff.json

[+] COMPLETE   1/1 contract   1/1 check   2.4s

Outputs   .apm/chains/20261022T180934Z-49ff1100dc06/artifacts/
          handoff.json
Receipt   .apm/chains/20261022T180934Z-49ff1100dc06/receipt.intoto.jsonl
          provenance  in-toto + SLSA v1
          checks      in-toto test-result (1)
          inventory   CycloneDX 1.5 (1 component)
          outputs     1 delivered file in outputs/
          unsigned: binds content, not identity; review before sharing

Next      apmx audit .apm/chains/20261022T180934Z-49ff1100dc06/receipt.intoto.jsonl
[+] Checked here: receipt of record <work>/checkws-v071/.apm/chains/20261022T180934Z-49ff1100dc06/receipt.intoto.jsonl
```

The last line names the receipt of record. `<work>` stands for the scratch
directory of the capture.

## Exit codes

| Exit | Meaning |
| --- | --- |
| 1 | The producer receipt is invalid, has no `evidence/`, or its definition digest differs. |
| 2 | Usage error. |
| other | The codes of a run: 0 COMPLETE, 20 REJECTED, 21 UNPROVEN, 22 HALTED, 23 receipt export failed. See [`apmx`](/apmx/docs/reference/apmx/#outcomes-and-exit-codes). |

## Limits

- The checks still run with host access, on bytes the producer wrote.
- The harness and models in the new receipt are what the producer reported,
  not what `apmx check` observed.
