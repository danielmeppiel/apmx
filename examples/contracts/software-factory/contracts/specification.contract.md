---
needs:
  - request.json
  - plan.json
produces: spec.json
verify:
  contract: python3 -I -B checks/verify.py specification
---
Turn the admitted plan into a precise specification for the shipping library.
Target: native Copilot through apmx. Read request.json and plan.json. The fixed
request wins if generated plan text conflicts with it. Do not implement code.

Write spec.json with exactly these fields and the fixed interface values:

```json
{
  "function": "shipping_cost",
  "parameter": "weight_grams",
  "returns": "integer cents",
  "accepted_type": "int excluding bool",
  "range": [1, 5000],
  "tiers": [
    {"through": 1000, "cents": 500},
    {"through": 2000, "cents": 700},
    {"through": 3000, "cents": 900},
    {"through": 4000, "cents": 1100},
    {"through": 5000, "cents": 1300}
  ],
  "errors": {"wrong_type": "TypeError", "out_of_range": "ValueError"},
  "requirements": ["types", "range", "rates", "errors", "purity"],
  "cases": [
    {"id": "minimum", "input": 1, "expected": {"returns": 500}, "requirements": ["range", "rates"], "reason": "The smallest supported integer weight"}
  ]
}
```

The one illustrated case is insufficient. Derive 7-32 concrete examples,
including inputs 1, 1000, 1001, 5000, 0, 5001, and true. Explain the boundary
or requirement each case establishes. All case objects have exactly the five
illustrated fields; expected is either {"returns": integer} or
{"raises": "TypeError"} / {"raises": "ValueError"}. Case IDs use lowercase
letters, digits and hyphens, begin with a letter, are unique and <=40 characters.
Reasons are nonempty and <=800 characters. Distinguish booleans from integers.

Do not change supplied files, execute checks/commands, install, or delegate.
Use the permitted file tools to write only spec.json, under 32 KiB.
