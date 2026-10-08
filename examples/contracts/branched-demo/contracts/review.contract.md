---
needs: [documentation.md, tests.md]
produces: review.md
verify:
  review-shape: python3 -I -B checks/always_pass.py review.md
---
Synthetic fork/join fixture contract for the Textual design-review loop
(docs/textual-design.md). Not a real product artifact; write a one-line
review.md placeholder.
