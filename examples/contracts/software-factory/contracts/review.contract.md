---
needs:
  - request.json
  - plan.json
  - spec.json
  - shipping.py
  - tests.json
  - evidence.json
produces: review.json
verify:
  contract: python3 -I -B checks/verify.py review
---
Give a fresh-context advisory review of this tiny shipping-library change.
Target: native Copilot through apmx. Read all supplied inputs. Compare the plan,
specification, actual implementation and tests to request.json. evidence.json
contains host-derived observations; do not invent checks, compute hashes in
prose, or treat the earlier agent's completion claim as proof.

Write review.json with exactly these fields:

```json
{
  "advisory": true,
  "assurance": "UNPROVEN",
  "recommendation": "no_findings",
  "summary": "Your assessment, grounded in the supplied implementation and observations",
  "findings": [],
  "limitations": ["Separate passing checks do not establish host isolation or production readiness."],
  "follow_up": []
}
```

Use "follow_up" instead of "no_findings" if you identify a concern. Each finding
has exactly file, line, severity, requirements, detail. For example:

```json
{"file": "shipping.py", "line": 1, "severity": "low", "requirements": ["purity"], "detail": "A specific concern grounded in this line, if one actually exists"}
```

Do not invent a finding to fill the template. Allowed files: plan.json,
spec.json, shipping.py, tests.json. Use actual 1-based line numbers and severity
low, medium or high. Requirement IDs: types, range, rates, errors, purity.
At most eight findings and follow-ups; limitations has 1-8 items; all text is
nonempty and <=800 characters. A no_findings report has no findings; follow_up
requires at least one finding or follow-up item.

Review quality is judgment: the checker validates format and evidence
references, not your reasoning. This report never authorizes merge/deployment
and is not a certification. Current execution remains UNPROVEN on the host.
Do not change inputs, run commands, install, or delegate. Write only review.json
under 32 KiB with the permitted file tools.
