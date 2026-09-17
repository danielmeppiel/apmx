---
needs: request.md
produces: plan.md
verify:
  plan-sections: python3 -I -B checks/documents.py planning plan.md
---
Plan the checkout change described in request.md. The request governs the
feature; your plan proposes the work, not a new business rule.

Write a concise plan.md with these nonempty Markdown sections:

## Goal
Describe the requested delivery policy and what must remain compatible.

## Changes
Explain the changes to pricing and checkout, and the new regression tests.
Leave implementation to the next contracts.

## Validation
Identify the threshold, neighboring values, zero, negatives and invalid types
that independent checks should exercise.

## Risks
Describe relevant correctness risks, including inconsistent pricing and totals.

Target native Copilot through APMX. Use the permitted file tools to write the
artifact. Do not modify inputs, run commands or checks, install, or delegate.
Do not claim completed implementation or observed test results. Keep ASCII
Markdown under 16 KiB. The document check validates sections, not plan quality.
