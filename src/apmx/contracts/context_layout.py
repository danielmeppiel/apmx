"""Canonical native-skill names and captured context destinations."""

import re
from typing import TypeGuard

from .models import ContractError, ImportedSkill

NATIVE_SKILL_ROOTS = (".agents/skills", ".github/skills", ".claude/skills")


def is_native_skill_name(name: object) -> TypeGuard[str]:
    """Recognize the same bounded identifier in imports and native observations."""
    return (
        isinstance(name, str) and len(name) <= 64
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None
    )


def native_skill_name(name: str) -> str:
    """Require a portable Agent Skills identifier without rewriting source identity."""
    if not is_native_skill_name(name):
        raise ContractError(
            "Native skill names require 1-64 lowercase letters, digits and single hyphens.",
            code="invalid_import",
        )
    return name


def is_native_skill_path(name: str) -> bool:
    """Identify any project skill discovery tree, including case aliases."""
    folded = name.casefold()
    return any(folded == root or folded.startswith(root + "/") for root in NATIVE_SKILL_ROOTS)


def context_directory(context: ImportedSkill, index: int) -> str:
    """Preserve native skill discovery while keeping instruction imports passive."""
    if context.kind == "skill":
        return f"{NATIVE_SKILL_ROOTS[0]}/{native_skill_name(context.context_name or context.name)}"
    return f"_apmx_context/import-{index}"
