---
needs:
  - request.md
  - specification.md
  - changes.diff
  - implementation.md
produces:
  - documentation.diff
  - documentation.md
verify:
  documentation-format-links-examples: python3 -I -B checks/documentation.py changes.diff documentation.diff
  documented-checkout: python3 -I -B checks/documented_checkout.py changes.diff documentation.diff
  documentation-report-format: python3 -I -B checks/documents.py documentation documentation.md
---
Update the actual project page docs/checkout.md for the admitted checkout
change. Read request.md, specification.md, changes.diff, implementation.md
and the captured page docs/checkout.md. The request and supplied acceptance
cases remain authoritative. Both declared outputs are new files:
documentation.md and documentation.diff are not inputs to read. The new
documentation.md is a report, not a substitute for updating the project page.

Read the common, Documentation report and Project page formats in
checks/document-formats.md. Use the supplied write_file artifact tool to edit
only docs/checkout.md and to write documentation.md. Keep the Delivery policy,
Examples and Input errors sections useful to a reader. Document the inclusive
threshold, cents, totals and type/error behavior. Populate the declared example
table from the canonical cases, with valid local links and H2 anchors.

Write documentation.md with the required JSON metadata and nonempty Changes,
Validation and Limitations sections. Explain the actual page edits, what the
independent documentation and combined-candidate checks assess, and limitations.
Do not claim observed executions: no test results are supplied to this stage.

After editing, invoke the supplied export_changes tool exactly once with
{"output":"documentation.diff"}. Export actual page edits, not hand-authored
patch hunks. Keep changes.diff, application source, tests and check resources
unchanged. Do not apply the code patch, run commands/checks, install or delegate.
If a tool reports an error, stop rather than inventing an output.

The independent checkers apply changes.diff first, then documentation.diff,
to a fresh captured baseline. They require identical accepted non-doc files
and rerun the supplied acceptance/regression suites on the combined candidate.
Passing these checks is not host isolation or semantic certification.
Keep the page and report ASCII and under 16 KiB each; keep the patch under
128 KiB. APMX publishes only the declared patch and report, not the working tree.
