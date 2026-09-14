---
needs:
  - request.json
  - plan.json
  - spec.json
produces: shipping.py
verify:
  contract: python3 -I -B checks/verify.py build
---
Implement the admitted shipping specification as shipping.py. Target: native
Copilot through apmx. Read request.json, plan.json and spec.json. The fixed
request and independent checks are authoritative; do not weaken requirements.

Define exactly one function: shipping_cost(weight_grams). Return integer cents
for each inclusive weight tier. Reject wrong types, including bool, with
TypeError; reject out-of-range integers with ValueError. Validate type before
comparing weights. This is real callable Python, not Markdown or a plan.

For this tiny teaching task, use a deliberately small branch-only subset:

- One ordinary function, one positional parameter, no annotations or defaults.
- Optional module/function docstrings.
- if / elif / else, comparisons, and / or, return a literal integer, raise
  TypeError or ValueError with an optional literal string message.
- The only permitted inspection call is type(weight_grams); compare it with int
  using is / is not. Return the explicit rate for each branch.
- Names are limited to shipping_cost (the definition), weight_grams, type, int,
  TypeError, ValueError. Integer literals are nonnegative and <=1000000.
- No assignments, arithmetic, unary operators, imports, attributes, indexing,
  loops, comprehensions, decorators, nested definitions, or other calls.

Keep source ASCII, under 8 KiB and 256 AST nodes; docstrings/messages <=400
characters. These task-specific restrictions are not a sandbox. An independent
checker executes the accepted function against all 5000 valid weights plus
fixed invalid-type/range cases. Generated tests are not its correctness oracle.

Do not run checks, shell commands, install, delegate, or modify inputs/checkers.
Write only shipping.py with the permitted file tools.
