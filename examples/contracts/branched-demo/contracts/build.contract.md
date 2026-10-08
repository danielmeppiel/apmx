---
needs: [specification.md, design.md]
produces: changes.diff
verify:
  build-shape: python3 -I -B checks/always_pass.py changes.diff
---
Synthetic fork/join fixture contract for the Textual design-review loop
(docs/textual-design.md). Not a real product artifact; write a one-line
changes.diff placeholder.
