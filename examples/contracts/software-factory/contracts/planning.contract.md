---
needs: request.json
produces: plan.json
verify:
  contract: python3 -I -B checks/verify.py planning
---
Plan the small shipping-library change in request.json. This is the planning
phase of a fixed five-phase example targeting native Copilot through apmx.
Do not implement the function or claim that work/checks have already completed.

Read request.json. Produce plan.json as a JSON object with exactly these fields:

```json
{
  "goal": "A concrete description of the intended shipping library",
  "steps": [
    {"id": "specify", "phase": "specification", "action": "What to specify", "requirements": ["types", "range", "rates", "errors"]},
    {"id": "implement", "phase": "build", "action": "How to implement the branch-only function", "requirements": ["purity"]},
    {"id": "test", "phase": "test", "action": "Which boundary and wrong-type cases to exercise", "requirements": ["types", "range", "rates", "errors"]},
    {"id": "review", "phase": "review", "action": "What the advisory review should inspect", "requirements": ["purity"]}
  ],
  "risks": ["A specific implementation risk and how separate checks will expose it"],
  "validation": "A concrete independent acceptance and test-adequacy strategy"
}
```

Replace the descriptive strings with your actual plan. Keep exactly four steps
in that order, unique step IDs, and coverage of all five requirement IDs. Text
fields must be nonempty and at most 800 characters; risks must contain 1-8 items.
Plan meaningful work, not status placeholders. Inputs and checks are not yours
to change. Do not run commands, install tools, delegate, or add phases.
Write only plan.json using the permitted file tools; keep it under 32 KiB.
