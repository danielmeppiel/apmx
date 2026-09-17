---
needs:
  - request.md
  - plan.md
  - src/pricing.py
  - src/checkout.py
  - checks/features/free-shipping.feature
produces: specification.md
verify:
  specification-sections: python3 -I -B checks/documents.py specification specification.md
---
Turn the admitted plan into a precise, concise specification for the checkout
feature. Read request.md, plan.md, the source and supplied Gherkin. The request
and supplied acceptance examples win if the plan conflicts with them.

Write specification.md with these nonempty Markdown sections:

## Behavior
Define the inclusive free-delivery threshold, the unchanged delivery fee below
it, zero handling and the relation between delivery and the checkout total.

## Interface
Record the two public functions, their existing argument and return shapes,
integer-cent semantics, and the required exceptions for negative and invalid
inputs, explicitly including booleans.

## Acceptance
Explain the supplied boundary and invalid-input examples and the new
unittest regression coverage. Do not replace or weaken the supplied feature.

Target native Copilot through APMX. Write the artifact with the permitted file
tools. Do not modify inputs, implement code, run commands/checks, install or
delegate. Do not claim observed executions. Keep ASCII Markdown under 16 KiB.
The document check validates sections; actual candidate checks decide behavior.
