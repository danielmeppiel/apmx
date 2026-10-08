"""Trivial always-pass check for the branched-demo DAG fixture.

This fixture only exists to exercise fork/join graph rendering in the
Textual preview (docs/textual-design.md); it makes no product claims.
"""

import sys

if __name__ == "__main__":
    print("always_pass: ok", *sys.argv[1:])
    sys.exit(0)
