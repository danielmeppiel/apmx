"""Launch only the supplied feature, ignoring all external Behave configuration."""

import sys
from pathlib import Path

from behave.configuration import Configuration
from behave.runner import Runner


def main() -> int:
    candidate, report = map(Path, sys.argv[1:])
    feature = Path(__file__).resolve().parent / "features/free-shipping.feature"
    config = Configuration(
        [
            str(feature),
            "--format",
            "json",
            "--outfile",
            str(report),
            "--no-summary",
            "--no-snippets",
            "--no-capture",
            "--no-capture-stderr",
            "--no-logcapture",
            "-D",
            f"candidate={candidate}",
        ],
        load_config=False,
    )
    config.stop = False
    return int(Runner(config).run())


if __name__ == "__main__":
    raise SystemExit(main())
