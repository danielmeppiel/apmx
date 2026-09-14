---
needs:
  - request.md
  - specification.md
  - changes.diff
  - implementation.md
produces: review.md
verify:
  document: python3 -I -B checks/documents.py review review.md
---
Give a fresh-context advisory review of the supplied checkout change. Compare
the specification, actual patch and implementation report with request.md.
Inspect the inclusive threshold, consistency of pricing and checkout totals,
input validation, and added regression coverage.

Write review.md with these nonempty Markdown sections:

## Assessment
Explain whether the proposed edits address the requested behavior and interface.

## Findings
Give concrete concerns with file and code references, or state that inspection
found none. Do not invent a concern to fill the section. Suggest relevant
follow-up work without treating advice as an acceptance decision.

## Limitations
Distinguish code inspection from observed executions. You receive artifacts,
not runtime records; the implementation report is not test evidence. Separate
passing checks do not establish host isolation or production readiness.

Target native Copilot through APMX. Do not change inputs, apply the patch, run
commands/checks, install, or delegate. Write ASCII Markdown under 16 KiB using
the permitted file tools. Do not certify, authorize merge/deployment or claim
you ran tests. The document checker validates sections, not review quality.
