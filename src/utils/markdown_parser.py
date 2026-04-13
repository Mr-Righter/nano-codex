"""Utilities for parsing markdown files with YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypeVar
from xml.sax.saxutils import escape

import yaml
from pydantic import BaseModel, Field


class MarkdownParseError(Exception):
    """Exception raised when markdown parsing fails."""


class FrontmatterDocument(BaseModel):
    """Shared base model for markdown-backed definitions."""

    instructions: str
    path: str | Path | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        field_names = set(type(self).model_fields)
        metadata = kwargs.pop("metadata", {}) or {}
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict")
        data = {key: value for key, value in kwargs.items() if key in field_names}
        extras = {key: value for key, value in kwargs.items() if key not in field_names}
        data["metadata"] = {**metadata, **extras}
        super().__init__(**data)

    def to_xml(
        self,
        fields: list[str],
        root_tag: str | None = None,
    ) -> str:
        """Serialize selected fields into a compact XML fragment."""

        field_tags = [
            f"<{field}>{escape(str(getattr(self, field, 'Unknown')))}</{field}>"
            for field in fields
        ]
        if root_tag:
            field_tags = ["\t" + tag for tag in field_tags]
            content = "\n".join(field_tags)
            return f"<{root_tag}>\n{content}\n</{root_tag}>"
        return "\n".join(field_tags)
            


class AgentDefinition(FrontmatterDocument):
    """Shared runtime definition for one agent markdown file."""

    name: str | None = None
    description: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    mcp_service: list[str] | None = None
    skills: list[str] | None = None
    hidden_skills: list[str] | None = None
    default_options: dict[str, Any] = Field(default_factory=dict)


class SkillDefinition(FrontmatterDocument):
    """Shared runtime definition for one skill markdown file."""

    name: str | None = None
    description: str | None = None
    invoke_when: str | None = None
    hidden: bool = False


T = TypeVar("T", bound=FrontmatterDocument)


class MarkdownParser:
    """Parser for markdown files with YAML frontmatter."""

    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n?(.*)$",
        re.DOTALL | re.MULTILINE,
    )

    @classmethod
    def split_frontmatter(
        cls,
        content: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return None, content.strip() or None

        try:
            frontmatter = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise MarkdownParseError(f"Invalid YAML frontmatter: {exc}") from exc

        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            raise MarkdownParseError("Frontmatter must be a YAML object")
        return frontmatter, match.group(2).strip() or None

    @classmethod
    def parse_file(
        cls,
        file_path: str | Path,
        document_type: type[T],
    ) -> T:
        path = Path(file_path)
        if not path.exists():
            raise MarkdownParseError(f"File not found: {path}")
        if not path.is_file():
            raise MarkdownParseError(f"Path is not a file: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise MarkdownParseError(f"Failed to read {path}: {exc}") from exc

        return cls.parse_content(content, document_type).model_copy(update={"path": path})

    @classmethod
    def parse_content(
        cls,
        content: str,
        document_type: type[T],
    ) -> T:
        frontmatter, body = cls.split_frontmatter(content)
        if frontmatter is None or body is None:
            raise MarkdownParseError("File must have both frontmatter and body content")

        data = dict(frontmatter)
        data["instructions"] = body

        try:
            return document_type(**data)
        except Exception as exc:
            raise MarkdownParseError(f"Invalid {document_type.__name__}: {exc}") from exc


def parse_agent_definition_file(agent_file: str | Path) -> AgentDefinition:
    """Parse one markdown agent file into a shared runtime definition."""

    return MarkdownParser.parse_file(agent_file, AgentDefinition)


def parse_skill_definition_file(skill_file: str | Path) -> SkillDefinition:
    """Parse one markdown skill file into a shared runtime definition."""

    return MarkdownParser.parse_file(skill_file, SkillDefinition)


__all__ = [
    "AgentDefinition",
    "FrontmatterDocument",
    "MarkdownParseError",
    "MarkdownParser",
    "SkillDefinition",
    "parse_agent_definition_file",
    "parse_skill_definition_file",
]
