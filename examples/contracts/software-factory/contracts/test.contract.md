---
needs:
  - request.json
  - spec.json
  - shipping.py
produces: tests.json
verify:
  contract: python3 -I -B checks/verify.py test
---
Design executable black-box JSON test cases for the captured shipping library.
Target: native Copilot through apmx. Read request.json, spec.json and shipping.py.
Expected results come from the request, not merely from what the code does.
Do not modify the implementation or claim that you executed tests.

Write tests.json with exactly this structure:

```json
{
  "strategy": "Describe boundary partitioning, type checks, and the mistakes these cases expose",
  "cases": [
    {"id": "minimum", "input": 1, "expected": {"returns": 500}, "requirements": ["range", "rates"], "reason": "The smallest supported weight"}
  ]
}
```

The illustrated single case is insufficient. Include each of these distinct
JSON inputs in a total of 23-32 cases:

```json
[-1, 0, 1, 2, 999, 1000, 1001, 1999, 2000, 2001, 2999, 3000, 3001, 3999, 4000, 4001, 4999, 5000, 5001, true, 1.5, "1000", null]
```

Each case has exactly id, input, expected, requirements, reason. Expected is
{"returns": integer cents} or {"raises": "TypeError"} / {"raises": "ValueError"}.
Use unique lowercase letter/digit/hyphen IDs, starting with a letter, <=40
characters. Requirement IDs are types, range, rates, errors, purity; select
the relevant ones for each case. Explain each case concisely. Text <=800
characters, numeric inputs within +/-1000000, strings <=64 characters.

The separate checker compares expectations to a fixed oracle, executes these
cases on shipping.py, and requires them to expose four known mistakes:
bool-as-int acceptance, wrong tier boundaries, zero accepted, maximum excluded.
Do not generate Python or shell commands. Use only permitted file tools to
write tests.json under 32 KiB. Do not run commands, install, or delegate.
