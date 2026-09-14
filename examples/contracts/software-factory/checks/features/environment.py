"""Import the actual reconstructed application, not a duplicate pricing rule."""

import importlib
import sys
from pathlib import Path
from typing import Any


def before_all(context: Any) -> None:
    candidate = Path(context.config.userdata["candidate"]).resolve()
    sys.path.insert(0, str(candidate))
    context.pricing = importlib.import_module("src.pricing").delivery_fee
    context.checkout = importlib.import_module("src.checkout").checkout
