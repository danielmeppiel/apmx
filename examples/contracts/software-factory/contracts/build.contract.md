---
needs:
  - request.md
  - specification.md
  - src/__init__.py
  - src/pricing.py
  - src/checkout.py
  - tests/test_checkout.py
  - checks/features/free-shipping.feature
produces:
  - changes.diff
  - implementation.md
verify:
  acceptance: python3 -I -B checks/acceptance.py changes.diff
  regression: python3 -I -B checks/regression.py changes.diff
  report: python3 -I -B checks/documents.py implementation implementation.md
---
Implement the admitted checkout specification. Read the request, specification,
source, existing tests and supplied feature. The request and supplied acceptance
remain authoritative if generated specification text conflicts with them.

Edit the actual private working copies of src/pricing.py and src/checkout.py.
Use apmx_artifacts.write_file with the file's path and complete content.
Use workspace-relative paths for every tool call.
Make delivery free from a 5000-cent subtotal and keep the 500-cent fee below
that threshold. Keep pricing and checkout totals consistent. Preserve the
documented interfaces, zero handling and validation of negatives and invalid
types, including booleans.

Add tests/test_free_shipping.py using Python's standard unittest framework.
Exercise the threshold and neighboring values, with useful regression coverage
for the requested behavior. Do not change the existing tests or supplied checks.

Use apmx_artifacts.write_file to write implementation.md with these nonempty
Markdown sections:

## Changes
Explain the source changes and added regression coverage.

## Validation
Describe what the new tests cover and what the separate supplied acceptance
and regression commands will assess. Do not claim you ran them.

## Limitations
Record relevant limitations or follow-up work without claiming certification.

After all file edits, invoke apmx_artifacts.export_changes exactly once with
{"output":"changes.diff"}. This bounded operation exports your actual edits
against the captured baseline. Do not hand-author patch hunks, invoke Git
through a shell, or substitute a narrative for the patch. Modified source files
are working copies, not additional published artifacts. If any tool reports an
error, stop; do not invent missing outputs or continue issuing write/export calls.

Target native Copilot through APMX. Use permitted file tools and the bounded
export operation only. Do not run commands/checks, install packages or delegate.
Keep source, tests and artifacts ASCII. Keep changes.diff under 128 KiB and
implementation.md under 16 KiB. APMX verifies the captured patch and report
independently; producing the files alone does not establish correctness.
