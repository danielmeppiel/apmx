"""Textual presentation prototype (docs/textual-design.md, milestone 1).

This package renders the real resolved contract graph for interactive
inspection. It does not execute contracts, retry anything, read secrets or
maintain a second evidence policy: the engine, ``ContractLogger`` and
``records.py`` remain the sole owners of outcomes and retained evidence.
Live-execution wiring (stdin/PTY consent handoff, streamed activity, checks
and evidence views) is tracked separately in docs/textual-design.md.
"""
