---
needs: changes.diff
produces: tests.md
verify:
  tests-shape: python3 -I -B checks/always_pass.py tests.md
---
Synthetic fork/join fixture contract for the Textual design-review loop
(docs/textual-design.md). Not a real product artifact; write a one-line
tests.md placeholder.
