---
needs: request.md
produces: plan.md
verify:
  plan-sections: python3 -I -B checks/documents.py planning plan.md
---
Plan the checkout change described in request.md. The request governs the
feature; your plan proposes the work, not a new business rule.
Read the common and Planning formats in checks/document-formats.md. Start
plan.md with the required JSON metadata, referencing the captured acceptance
IDs and distinguishing existing file targets from the proposed new test.

Write a concise plan.md with these nonempty Markdown sections:

## Goal
Describe the requested delivery policy and what must remain compatible.

## Changes
Explain the changes to pricing, checkout, regression tests and docs/checkout.md.
Leave implementation to the next contracts.

## Validation
Identify the threshold, neighboring values, zero, negatives and invalid types
that independent checks should exercise.

## Risks
Describe relevant correctness risks, including inconsistent pricing and totals.

Use the permitted file tools supplied by APMX to write the
artifact. Do not modify inputs, run commands or checks, install, or delegate.
Do not claim completed implementation or observed test results. Keep ASCII
Markdown under 16 KiB. Format and reference checks do not establish plan quality.
