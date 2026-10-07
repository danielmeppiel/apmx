"""Static ownership guards for contract presentation, not a general Python analyzer."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ROOT = Path(__file__).resolve().parents[2] / "src/apmx"
LOGGER = "core/contract_logger.py"
PRESENTER = "_ContractDisplay"
CAPABILITY_KEYS = {"NO_COLOR", "CI", "TERM", "APM_PROGRESS"}
TERMINAL_FUNCTIONS = {
    "_plain_echo",
    "_rich_echo",
    "_rich_error",
    "_rich_info",
    "_rich_success",
    "_rich_warning",
}


def _import_module(node: ast.ImportFrom, relative: str) -> str:
    if not node.level:
        return node.module or ""
    package = ["apmx", *Path(relative).with_suffix("").parts[:-1]]
    prefix = package[: len(package) - node.level + 1]
    return ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")


def _bindings(tree: ast.Module, relative: str) -> dict[str, str]:
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                names[item.asname or item.name.split(".")[0]] = (
                    item.name if item.asname else item.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            module = _import_module(node, relative)
            for item in node.names:
                names[item.asname or item.name] = f"{module}.{item.name}"
    return names


def _qualified(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, names)
        return f"{parent}.{node.attr}" if parent else ""
    if isinstance(node, ast.Call):
        return _qualified(node.func, names)
    return ""


def _terminal_call(name: str) -> bool:
    return (
        name in {"print", "click.echo", "click.secho", "sys.stdout.write", "sys.stderr.write"}
        or name.endswith(".print")
        or name.rsplit(".", 1)[-1] in TERMINAL_FUNCTIONS
    )


def _violations(source: str, relative: str) -> list[str]:
    tree = ast.parse(source)
    names = _bindings(tree, relative)
    domain = relative == "cli.py" or relative.startswith("contracts/")
    findings: list[str] = []

    def report(node: ast.AST, message: str) -> None:
        findings.append(f"{relative}:{node.lineno}: {message}")

    def walk(node: ast.AST, classes: tuple[str, ...] = ()) -> None:
        if isinstance(node, ast.ClassDef):
            classes = (*classes, node.name)
        presenter = relative == LOGGER and classes == (PRESENTER,)
        if domain and isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [_import_module(node, relative)]
            )
            if isinstance(node, ast.ImportFrom):
                modules.extend(
                    f"{_import_module(node, relative)}.{item.name}" for item in node.names
                )
            if any(
                name == prefix or name.startswith(prefix + ".")
                for name in modules
                for prefix in ("rich", "colorama", "apmx.utils.console", "apmx.utils.install_tui")
            ):
                report(node, "execution imports terminal rendering instead of ContractLogger")
            if (
                isinstance(node, ast.ImportFrom)
                and _import_module(node, relative) == "apmx.core.contract_logger"
                and any(item.name != "ContractLogger" for item in node.names)
            ):
                report(node, "execution imports private presentation policy")

        if isinstance(node, ast.Call):
            function = _qualified(node.func, names)
            if _terminal_call(function) and (domain or (relative == LOGGER and not presenter)):
                report(node, "human terminal writes must route through the presenter")
            if relative == LOGGER and not presenter:
                if any(
                    item.arg in {"color", "style", "accent"}
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                    and item.value.value
                    for item in node.keywords
                ):
                    report(node, "literal visual styling belongs to the presenter policy")
                if (
                    function.rsplit(".", 1)[-1] == "_write"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == ""
                ):
                    report(node, "visible block gaps belong to the presenter")
            if relative == LOGGER:
                if function.endswith((".isatty", ".get_terminal_size")):
                    report(node, "terminal capabilities belong to utils.console")
                if (
                    function in {"os.getenv", "os.environ.get"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in CAPABILITY_KEYS
                ):
                    report(node, "terminal environment policy belongs to utils.console")
            if presenter and (
                function.startswith(
                    (
                        "json.",
                        "subprocess.",
                        "apmx.contracts.records.",
                        "apmx.contracts.engine.",
                        "apmx.contracts.workspace.",
                    )
                )
                or function == "open"
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr
                    in {"read_text", "read_bytes", "write_text", "write_bytes", "open"}
                )
            ):
                report(node, "presenter must not read evidence, parse results or execute work")

        if (
            relative == LOGGER
            and isinstance(node, ast.Compare)
            and any(_qualified(item, names) == "os.environ" for item in node.comparators)
            and isinstance(node.left, ast.Constant)
            and node.left.value in CAPABILITY_KEYS
        ):
            report(node, "terminal environment policy belongs to utils.console")
        if (
            relative == LOGGER
            and isinstance(node, ast.Subscript)
            and (
                _qualified(node.value, names) == "os.environ"
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in CAPABILITY_KEYS
            )
        ):
            report(node, "terminal environment policy belongs to utils.console")
        for child in ast.iter_child_nodes(node):
            walk(child, classes)

    walk(tree)
    return findings


def test_contract_execution_and_presentation_keep_their_boundaries() -> None:
    paths = [ROOT / "cli.py", ROOT / LOGGER, *sorted((ROOT / "contracts").rglob("*.py"))]
    findings = []
    for path in paths:
        findings.extend(
            _violations(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
        )
    assert not findings, "\n".join(findings)


def test_logger_has_one_explicit_terminal_presenter() -> None:
    tree = ast.parse((ROOT / LOGGER).read_text(encoding="utf-8"))
    owners = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == PRESENTER
    ]
    assert len(owners) == 1


@pytest.mark.parametrize(
    ("relative", "source", "reason"),
    [
        ("contracts/chain.py", "import rich", "execution imports terminal"),
        (
            "contracts/chain.py",
            "from ..utils import console as ui",
            "execution imports terminal",
        ),
        (
            "contracts/chain.py",
            "from ..core.contract_logger import _ContractDisplay",
            "private presentation",
        ),
        ("cli.py", "import click as c\nc.echo('finished')", "human terminal writes"),
        ("contracts/engine.py", "print('passed')", "human terminal writes"),
        (
            LOGGER,
            (
                "from apmx.utils import console as ui\n"
                "class ContractLogger:\n"
                "    def emit(self):\n"
                "        ui._rich_echo('done')\n"
            ),
            "human terminal writes",
        ),
        (
            LOGGER,
            (
                "class _LiveProducerDisplay:\n"
                "    def render(self):\n"
                "        Text('active', style='cyan')\n"
            ),
            "literal visual styling",
        ),
        (
            LOGGER,
            "class ContractLogger:\n    def start(self):\n        self._write('')\n",
            "visible block gaps",
        ),
        (
            LOGGER,
            (
                "import os\n"
                "class ContractLogger:\n"
                "    def color(self):\n"
                "        return os.getenv('NO_COLOR')\n"
            ),
            "terminal environment policy",
        ),
        (
            LOGGER,
            (
                "import os\n"
                "class ContractLogger:\n"
                "    def color(self):\n"
                "        return 'NO_COLOR' in os.environ\n"
            ),
            "terminal environment policy",
        ),
        (
            LOGGER,
            (
                "import os\n"
                "class ContractLogger:\n"
                "    def color(self):\n"
                "        return os.environ['TERM']\n"
            ),
            "terminal environment policy",
        ),
        (
            LOGGER,
            (
                "class ContractLogger:\n"
                "    def output(self, stream):\n"
                "        return stream.isatty()\n"
            ),
            "terminal capabilities",
        ),
        (
            LOGGER,
            (
                "import json as codec\n"
                "class _ContractDisplay:\n"
                "    def verdict(self, raw):\n"
                "        return codec.loads(raw)\n"
            ),
            "presenter must not",
        ),
        (
            LOGGER,
            (
                "from apmx.contracts import records as r\n"
                "class _ContractDisplay:\n"
                "    def verdict(self, result):\n"
                "        return r.reduce_outcome(result)\n"
            ),
            "presenter must not",
        ),
        (
            LOGGER,
            (
                "class _ContractDisplay:\n"
                "    def verdict(self, path):\n"
                "        return path.read_text()\n"
            ),
            "presenter must not",
        ),
        (
            LOGGER,
            (
                "import subprocess as process\n"
                "class _ContractDisplay:\n"
                "    def run(self):\n"
                "        process.run(['git'])\n"
            ),
            "presenter must not",
        ),
    ],
)
def test_guard_rejects_split_ownership(relative: str, source: str, reason: str) -> None:
    assert any(reason in item for item in _violations(source, relative))


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        (
            "contracts/chain.py",
            (
                "from ..core.contract_logger import ContractLogger as Logger\n"
                "def show(result):\n"
                "    Logger().render_chain_result(result)\n"
            ),
        ),
        (
            LOGGER,
            (
                "from apmx.utils import console as ui\n"
                "class _ContractDisplay:\n"
                "    def write(self, text):\n"
                "        ui._rich_echo(text, color='yellow')\n"
                "class ContractLogger:\n"
                "    def finished(self, result):\n"
                "        self._display.render_outcome(result.outcome)\n"
            ),
        ),
        (
            LOGGER,
            (
                "class ContractLogger:\n"
                "    def observe(self, text):\n"
                "        self._record(text)\n"
                "        self._display.emit_line(text)\n"
            ),
        ),
    ],
)
def test_guard_accepts_canonical_delegation(relative: str, source: str) -> None:
    assert _violations(source, relative) == []
