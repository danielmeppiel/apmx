"""Toy application used only by the enterprise-receiver security walkthrough.

This is a deliberately trivial fixture app, not production code. A software
factory is asked to change the greeting so it uses a full salutation.
"""


def greet(name: str) -> str:
    return f"Hi {name}"
