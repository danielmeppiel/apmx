---
needs:
  - request.md
  - specification.md
  - changes.diff
  - implementation.md
  - documentation.diff
  - documentation.md
produces: review.md
verify:
  review-format-references: python3 -I -B checks/documents.py review review.md
---
Give a fresh-context advisory review of the supplied checkout change. Compare
the specification, both patches and both change reports with request.md.
Inspect the inclusive threshold, consistency of pricing and checkout totals,
input validation, added regression coverage and the updated project documentation.
Read the common and Advisory review formats in checks/document-formats.md.
Start review.md with the required JSON metadata. Findings cite existing lines
of admitted artifacts, not invented candidate paths; an empty findings list is valid.

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

Do not change inputs, apply patches, run
commands/checks, install, or delegate. Write ASCII Markdown under 16 KiB using
the permitted file tools. Do not certify, authorize merge/deployment or claim
you ran tests. Format and reference checks do not establish review quality.
