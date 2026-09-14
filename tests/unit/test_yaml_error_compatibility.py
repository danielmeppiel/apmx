"""Round-trip YAML keeps its error family without swallowing unrelated failures."""

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from ruamel.yaml import YAMLError as RuamelYAMLError

from apmx.utils import yaml_io

pytestmark = pytest.mark.component


@pytest.mark.parametrize("operation", ["load", "dump"])
@pytest.mark.parametrize("error_type", [RuamelYAMLError, RuntimeError, TypeError, OSError])
def test_roundtrip_normalizes_only_yaml_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[Exception],
) -> None:
    path = tmp_path / "manifest.yml"
    original = b"# retained\nvalue: before\n"
    path.write_bytes(original)
    failure = error_type("controlled round-trip failure")
    engine = Mock()
    engine.load.side_effect = failure
    engine.dump.side_effect = failure
    monkeypatch.setattr(yaml_io, "_roundtrip_yaml", Mock(return_value=engine))
    expected = yaml.YAMLError if error_type is RuamelYAMLError else error_type
    with pytest.raises(expected) as caught:
        if operation == "load":
            yaml_io.load_yaml_roundtrip(path)
        else:
            yaml_io.dump_yaml_roundtrip({"value": "after"}, path)
    if error_type is RuamelYAMLError:
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
    assert path.read_bytes() == original


def test_roundtrip_still_preserves_comments_and_normalizes_newlines(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_bytes(b"# retained\r\nvalue: before\r\n")
    data = yaml_io.load_yaml_roundtrip(path)
    data["value"] = "after"
    yaml_io.dump_yaml_roundtrip(data, path)
    assert path.read_bytes() == b"# retained\nvalue: after\n"
