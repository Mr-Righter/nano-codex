"""Discovery helpers for markdown-backed agent and skill definitions."""

from __future__ import annotations

import logging
from pathlib import Path

from .markdown_parser import (
    AgentDefinition,
    MarkdownParseError,
    SkillDefinition,
    parse_agent_definition_file,
    parse_skill_definition_file,
)

logger = logging.getLogger(__name__)


def discover_agent_definitions(directory: str | Path) -> dict[str, AgentDefinition]:
    """Discover valid agent definitions under one directory."""

    root = Path(directory)
    definitions: dict[str, AgentDefinition] = {}
    if not root.exists() or not root.is_dir():
        return definitions

    for path in sorted(root.glob("*.md")):
        try:
            definition = parse_agent_definition_file(path)
            if not definition.description:
                raise MarkdownParseError(
                    f"Agent definition must include a description: {path}"
                )
            definitions[definition.name] = definition
        except MarkdownParseError as exc:
            logger.error("Failed to parse agent definition %s: %s", path, exc)

    return definitions


def discover_skill_definitions(directory: str | Path) -> dict[str, SkillDefinition]:
    """Discover valid skill definitions under one directory."""

    root = Path(directory)
    definitions: dict[str, SkillDefinition] = {}
    if not root.exists() or not root.is_dir():
        return definitions

    for path in sorted(root.glob("*/SKILL.md")):
        try:
            definition = parse_skill_definition_file(path)
            if not definition.description:
                raise MarkdownParseError(
                    f"Skill definition must include a description: {path}"
                )
            definitions[definition.name] = definition
        except MarkdownParseError as exc:
            logger.error("Failed to parse skill definition %s: %s", path, exc)

    return definitions


__all__ = [
    "discover_agent_definitions",
    "discover_skill_definitions",
]
