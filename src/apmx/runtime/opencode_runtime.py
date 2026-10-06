"""Bounded native OpenCode 1.2.24 startup; native configuration owns authentication."""

import json
import os
import re
import time
from pathlib import Path

from ..contracts import process
from ..contracts.context_layout import NATIVE_SKILL_ROOTS
from ..contracts.models import BaselineSnapshot, ContractError, LeafPlan, Outcome, ProcessRequest
from ..core.tls_trust import build_child_tls_env
from .artifact_tools import SERVER, TOOLS, configure_server, save_json
from .contract_prompt import contract_prompt

SUPPORTED_VERSION = "1.2.24"


class OpenCodeRuntime:
    def build_contract_request(
        self,
        plan: LeafPlan,
        snapshot: BaselineSnapshot,
        run_directory: Path,
        *,
        timeout_seconds: float,
    ) -> ProcessRequest:
        deadline = time.monotonic() + timeout_seconds
        env = dict(os.environ)
        if any(
            name in env
            for name in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "OPENCODE_PERMISSION")
        ):
            raise ContractError(
                "OpenCode inline/config-directory/permission overrides cannot be reconciled "
                "with this bounded native profile. No producer was launched.",
                code="native_configuration_unobservable",
                outcome=Outcome.UNPROVEN,
            )
        env.update(
            OPENCODE_AUTO_SHARE="false",
            OPENCODE_DISABLE_AUTOUPDATE="true",
            OPENCODE_DISABLE_PROJECT_CONFIG="true",
            OPENCODE_DISABLE_CLAUDE_CODE="true",
            OPENCODE_DISABLE_EXTERNAL_SKILLS="true",
            OPENCODE_DISABLE_LSP_DOWNLOAD="true",
            OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER="true",
        )
        env = build_child_tls_env(env)
        version = self._probe(plan, snapshot, ("--version",), env, deadline).strip()
        if version != SUPPORTED_VERSION.encode("ascii"):
            raise ContractError(
                f"This OpenCode contract profile requires native {SUPPORTED_VERSION}.",
                code="unsupported_native_version",
                outcome=Outcome.UNPROVEN,
            )
        initial = self._configuration(plan, snapshot, env, deadline)
        if initial.get("permission") not in (None, {}) or initial.get("tools") not in (None, {}):
            raise ContractError(
                "Configured OpenCode permissions/tools cannot be silently replaced by the "
                "bounded profile. No producer was launched; native policy was not changed.",
                code="native_configuration_unobservable",
                outcome=Outcome.UNPROVEN,
            )
        names = self._startup_names(initial)
        if SERVER in names:
            raise ContractError(
                "A configured MCP server collides with the private artifact tool server.",
                code="native_tool_collision",
            )
        config_root = self._config_root(plan, snapshot, env, deadline)
        prompt = config_root / "AGENTS.md"
        if prompt.exists() or prompt.is_symlink():
            raise ContractError(
                "OpenCode global AGENTS.md cannot be excluded by this native profile. "
                "No producer was launched; global instructions were not read or changed.",
                code="native_configuration_unobservable",
                outcome=Outcome.UNPROVEN,
            )
        command, environment = configure_server(plan, snapshot, run_directory)
        skill_names = [
            item.context_name or item.name for item in plan.imported_skills if item.kind == "skill"
        ]
        profile = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "snapshot": False,
            "formatter": False,
            "lsp": False,
            "skills": {
                "paths": [str(snapshot.producer / NATIVE_SKILL_ROOTS[0])] if skill_names else [],
                "urls": [],
            },
            "permission": {
                "*": "deny",
                "read": "allow",
                "skill": {"*": "deny", **{name: "allow" for name in skill_names}},
                **{f"{SERVER}_{tool}": "allow" for tool in TOOLS},
            },
            "mcp": {
                **{name: {"enabled": False} for name in names},
                SERVER: {
                    "type": "local",
                    "command": list(command),
                    "environment": environment,
                    "enabled": True,
                },
            },
        }
        configuration = run_directory / "native-tools/opencode.json"
        save_json(configuration, profile)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(profile)
        effective = self._configuration(plan, snapshot, env, deadline)
        effective_names = self._startup_names(effective)
        expected_names = tuple(sorted((*names, SERVER)))
        if effective_names != expected_names or any(
            effective.get(key) != profile[key]
            for key in (
                "permission",
                "skills",
                "share",
                "autoupdate",
                "snapshot",
                "formatter",
                "lsp",
            )
        ):
            raise self._unobservable()
        effective_servers = effective["mcp"]
        if effective_servers.get(SERVER) != profile["mcp"][SERVER] or any(
            effective_servers[name].get("enabled") is not False for name in names
        ):
            raise self._unobservable()
        argv = [
            str(plan.executable),
            "run",
            "--format",
            "json",
            contract_prompt(plan, snapshot, read_tool="read", artifact_prefix=f"{SERVER}_"),
            "--agent",
            "build",
        ]
        if plan.model is not None:
            argv.extend(("--model", plan.model))
        return ProcessRequest(
            tuple(argv),
            snapshot.producer,
            self._remaining(deadline),
            env=env,
            control_observations={
                "native_version": SUPPORTED_VERSION,
                "native_agent": "build",
                "artifact_tools": {
                    "server": SERVER,
                    "tools": TOOLS,
                    "configuration": str(configuration),
                    "scope": "shared bounded private working files and explicit Git export; not isolation",
                },
                "disabled_configured_mcp_servers": names,
                "startup_scope": (
                    "Native effective configuration verified before execution; project config and "
                    "external skill discovery disabled; selected skills supplied explicitly. "
                    "The invocation selects builtin build and denies delegation. Native internal "
                    "title/summary/compaction calls may still occur. Global prompt files, "
                    "active-agent overrides, plugins/instructions "
                    "and conflicting managed settings refuse. Host/native installation is not isolated."
                ),
                "model_observation": (
                    "Native JSONL does not report model identity. Requested selection is retained "
                    "separately; no default model or provider is inferred or substituted."
                ),
            },
        )

    @staticmethod
    def _unobservable() -> ContractError:
        return ContractError(
            "The effective OpenCode configuration could not establish the bounded profile. "
            "No producer was launched; native configuration was not changed.",
            code="native_configuration_unobservable",
            outcome=Outcome.UNPROVEN,
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ContractError(
                "The attempt watchdog expired during OpenCode preparation.", code="attempt_deadline"
            )
        return remaining

    @classmethod
    def _probe(
        cls,
        plan: LeafPlan,
        snapshot: BaselineSnapshot,
        args: tuple[str, ...],
        env: dict[str, str],
        deadline: float,
    ) -> bytes:
        output = bytearray()
        oversized = False

        def receive(stream: str, chunk: bytes) -> None:
            nonlocal oversized
            if stream != "stdout" or oversized:
                return
            if len(output) + len(chunk) > 256 * 1024:
                output.clear()
                oversized = True
            else:
                output.extend(chunk)

        observation = process.supervise_process(
            ProcessRequest(
                (str(plan.executable), *args),
                snapshot.producer,
                cls._remaining(deadline),
                env=env,
            ),
            on_bytes=receive,
            limits=plan.limits,
        )
        if (
            observation.returncode != 0
            or observation.error
            or observation.stop_reason
            or not observation.cleanup_confirmed
            or oversized
        ):
            raise ContractError(
                "OpenCode configuration observation failed or did not clean up. "
                "No producer was launched.",
                code="native_configuration_failed",
                outcome=Outcome.HALTED,
            )
        return bytes(output)

    @classmethod
    def _config_root(
        cls,
        plan: LeafPlan,
        snapshot: BaselineSnapshot,
        env: dict[str, str],
        deadline: float,
    ) -> Path:
        raw = cls._probe(plan, snapshot, ("debug", "paths"), env, deadline)
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise cls._unobservable() from None
        paths = {}
        for line in text.split("\n"):
            line = line.removesuffix("\r")
            if not line:
                continue
            match = re.fullmatch(
                r"(home|data|bin|log|cache|config|state)[ \t]+([^\x00-\x1f\x7f]+)", line
            )
            if match is None or match[1] in paths or not Path(match[2]).is_absolute():
                raise cls._unobservable()
            paths[match[1]] = match[2]
        if "config" not in paths:
            raise cls._unobservable()
        return Path(paths["config"])

    @classmethod
    def _json_probe(
        cls,
        plan: LeafPlan,
        snapshot: BaselineSnapshot,
        args: tuple[str, ...],
        env: dict[str, str],
        deadline: float,
    ) -> dict:
        raw = cls._probe(plan, snapshot, args, env, deadline)

        def unique(pairs: list[tuple[str, object]]) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate native configuration key.")
                result[key] = value
            return result

        def invalid_constant(value: str) -> None:
            raise ValueError("Invalid native JSON constant.")

        try:
            result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, TypeError, RecursionError):
            raise cls._unobservable() from None
        if not isinstance(result, dict):
            raise cls._unobservable()
        return result

    @classmethod
    def _configuration(
        cls,
        plan: LeafPlan,
        snapshot: BaselineSnapshot,
        env: dict[str, str],
        deadline: float,
    ) -> dict:
        return cls._json_probe(plan, snapshot, ("debug", "config"), env, deadline)

    @classmethod
    def _startup_names(cls, config: dict) -> tuple[str, ...]:
        agents = config.get("agent", {})
        modes = config.get("mode", {})
        if (
            config.get("plugin", []) != []
            or config.get("instructions", []) != []
            or not isinstance(agents, dict)
            or not isinstance(modes, dict)
            or any(
                name in agents or name in modes
                for name in ("build", "title", "summary", "compaction")
            )
            or config.get("default_agent") not in (None, "build")
        ):
            raise cls._unobservable()
        servers = config.get("mcp", {})
        if (
            not isinstance(servers, dict)
            or len(servers) > 128
            or any(
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:/@-]{0,255}", name)
                or not isinstance(value, dict)
                for name, value in servers.items()
            )
        ):
            raise cls._unobservable()
        return tuple(sorted(servers))
