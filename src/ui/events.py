"""Typed UI events shared by console and TUI renderers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UiEvent:
    """Base class for all UI events."""


@dataclass(frozen=True)
class UserMessageEvent(UiEvent):
    """User-authored chat message appended to the transcript."""

    text: str


@dataclass(frozen=True)
class AssistantTurnStarted(UiEvent):
    """Signal that one assistant turn has started rendering."""

    usage: dict | None = None


@dataclass(frozen=True)
class AssistantReasoningEvent(UiEvent):
    """Assistant reasoning/thinking text chunk."""

    text: str


@dataclass(frozen=True)
class AssistantTextEvent(UiEvent):
    """Assistant response text chunk intended for user display."""

    text: str


@dataclass(frozen=True)
class ToolCallStarted(UiEvent):
    """Tool-call header event emitted before tool execution completes."""

    name: str
    call_id: str
    args_str: str


@dataclass(frozen=True)
class ToolPresentationModel:
    """Normalized tool result display model consumed by console and TUI renderers."""

    kind: str
    summary: str
    body: str | None = None
    path: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class ToolResultEvent(UiEvent):
    """Completed tool result normalized into a presentation model."""

    tool_name: str
    call_id: str | None
    presentation: ToolPresentationModel


@dataclass(frozen=True)
class SubagentScopeStarted(UiEvent):
    """Begin grouping subsequent transcript items under a subagent scope."""

    pass


@dataclass(frozen=True)
class SubagentScopeEnded(UiEvent):
    """Close the currently active subagent transcript scope."""

    pass


@dataclass(frozen=True)
class SessionStarted(UiEvent):
    """Interactive session lifecycle start event."""

    pass


@dataclass(frozen=True)
class SessionEnded(UiEvent):
    """Interactive session lifecycle end event."""

    pass


@dataclass(frozen=True)
class SessionRestored(UiEvent):
    """Notice that a persisted session was restored from disk."""

    path: str | Path


@dataclass(frozen=True)
class SessionSaved(UiEvent):
    """Notice that the active session was saved to disk."""

    path: str | Path


@dataclass(frozen=True)
class InfoNotice(UiEvent):
    """Non-error informational notice for the active UI."""

    text: str


@dataclass(frozen=True)
class WarningNotice(UiEvent):
    """Warning notice intended for immediate user visibility."""

    text: str


@dataclass(frozen=True)
class CompactionSummaryEvent(UiEvent):
    """Summary of one completed compaction step."""

    total_tokens: int
    max_tokens: int
    strategy: str
    remaining: int
    current_tokens: int = 0
    summary_text: str | None = None


EDIT_TOOL_NAMES: frozenset[str] = frozenset({"edit", "write"})
BASH_TOOL_NAMES: frozenset[str] = frozenset(
    {"bash", "bash_output", "kill_bash", "run_in_background"}
)
SUBAGENT_TOOL_NAME: str = "solve_task_with_subagent"


def extract_bash_display(raw_result: str) -> str:
    """Extract stdout and stderr from bash XML output for display."""
    stdout = ""
    stderr = ""

    stdout_match = re.search(r"<stdout>(.*?)</stdout>", raw_result, re.DOTALL)
    if stdout_match:
        stdout = stdout_match.group(1).strip()

    stderr_match = re.search(r"<stderr>(.*?)</stderr>", raw_result, re.DOTALL)
    if stderr_match:
        stderr = stderr_match.group(1).strip()

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    return "\n".join(parts) if parts else raw_result


__all__ = [
    "AssistantReasoningEvent",
    "AssistantTextEvent",
    "AssistantTurnStarted",
    "BASH_TOOL_NAMES",
    "CompactionSummaryEvent",
    "EDIT_TOOL_NAMES",
    "InfoNotice",
    "SessionEnded",
    "SessionRestored",
    "SessionSaved",
    "SessionStarted",
    "SUBAGENT_TOOL_NAME",
    "SubagentScopeEnded",
    "SubagentScopeStarted",
    "ToolCallStarted",
    "ToolPresentationModel",
    "ToolResultEvent",
    "UiEvent",
    "UserMessageEvent",
    "WarningNotice",
    "extract_bash_display",
]
