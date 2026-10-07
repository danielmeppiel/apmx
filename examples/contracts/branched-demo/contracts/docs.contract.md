---
needs: changes.diff
produces: documentation.md
verify:
  docs-shape: python3 -I -B checks/always_pass.py documentation.md
---
Synthetic fork/join fixture contract for the Textual design-review loop
(docs/textual-design.md). Not a real product artifact; write a one-line
documentation.md placeholder.
