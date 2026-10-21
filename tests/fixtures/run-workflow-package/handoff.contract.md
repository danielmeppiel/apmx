---
needs: notes.md
produces: handoff.json
verify:
  identity: python3 checks/check.py handoff.json
---
Copy the JSON object in notes.md to handoff.json, unchanged.

This factory exercises `.github/workflows/run.yml` end to end with the
hermetic protocol actor (`scripts/fixtures/copilot_actor.py`): no model is
called. The actor also overwrites `checks/check.py` in its workspace, which
must not change what the separate check job runs.
