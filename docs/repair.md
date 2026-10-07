# Bounded candidate repair

A contract runs once unless its frontmatter explicitly sets both budget fields:

```yaml
budget:
  max_attempts: 3
  max_seconds: 600
```

`max_attempts` is an integer from 1 to 16. `max_seconds` is a finite positive
number up to 86400. There are no inferred retry defaults or model-spend caps.
The seconds allowance is shared across attempts and checks; existing per-run
and per-check limits still apply. Process cleanup and durable record retention
can extend beyond that allowance.

APMX retries only a completed delivery whose required checks all ran, with at
least one ordinary rejection (exit 1) and no operational check failures. Missing
outputs, exit 2 or unexpected check exits, native protocol errors, cancellation,
timeouts, changed resources, corrupt evidence and unconfirmed cleanup stop.
Producing the same rejected bytes again stops as `no_progress`. A changing but
still rejected candidate stops at `max_attempts`.

Every attempt starts from the same [captured original project](workspace.md),
contract, selected context and acceptance criteria. It receives the previous
captured artifacts under read-only `.apm/repair/` and a bounded diagnostic
excerpt. These are rejected references, not accepted inputs or prefilled output
files. All declared outputs must be delivered again and every required check
reruns independently. Code patches remain relative to the original baseline;
the protected reference directory is excluded from Git export. Document
rejections use the same controller.

Each attempt retains its own `.apm/runs/<id>/record.json` and `/1` identity.
The versioned `apmx-contract-controller/1` record under `.apm/controllers/`
links every attempt and records why execution stopped. Only the ordinary leaf
record authority can issue `COMPLETE`; the controller selects that result
without changing it. The result and downstream receipts also bind the exact
controller record. Changing the controller, an earlier attempt, its baseline
or retained artifacts invalidates a successful completion boundary. Local
records can contain diagnostic transcript excerpts; they are not a sanitized
sharing format.

The same behavior applies to standalone and factory execution. Native host
access still requires consent, and passing native outputs still require the
explicit native-assurance exception to cross a strict factory handoff.
Repair does not provide isolation, signed provenance or semantic certification.
Deterministic controller tests are not evidence that a model repaired a real task.
