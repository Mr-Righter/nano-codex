"""Transcript data model used by the Textual UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

from src.ui.events import ToolPresentationModel


@dataclass(frozen=True)
class AssistantTurnState:
    """Assistant turn metadata preserved as a merge boundary and audit trail."""

    id: int
    usage: dict | None = None


@dataclass
class TranscriptNode:
    """Stable transcript node stored independently from Textual widgets."""

    id: str
    kind: str
    parent_id: str | None = None
    text: str = ""
    usage: dict | None = None
    name: str | None = None
    call_id: str | None = None
    args_str: str | None = None
    presentation: ToolPresentationModel | None = None
    expanded: bool = False
    children: list[str] = field(default_factory=list)
    turn_id: int | None = None


@dataclass(frozen=True)
class _PendingToolResult:
    """Tool result cached until the matching tool-call node appears."""

    tool_name: str
    presentation: ToolPresentationModel


class TranscriptStore:
    """Tree-shaped transcript store with stable ids and tool lookup indices."""

    def __init__(self) -> None:
        self._id_counter = count(1)
        self.nodes: dict[str, TranscriptNode] = {}
        self.top_level_ids: list[str] = []
        self.call_id_to_node_id: dict[str, str] = {}
        self.turns: dict[int, AssistantTurnState] = {}
        self._pending_tool_results: dict[str, _PendingToolResult] = {}
        self._scope_stack: list[str] = []
        self._current_turn_id = 0

    def clear(self) -> None:
        self.__init__()

    @property
    def current_turn_id(self) -> int:
        return self._current_turn_id

    @property
    def current_turn(self) -> AssistantTurnState | None:
        return self.turns.get(self._current_turn_id)

    def begin_turn(self, *, usage: dict | None) -> None:
        self._current_turn_id += 1
        self.turns[self._current_turn_id] = AssistantTurnState(
            id=self._current_turn_id,
            usage=usage,
        )

    def append_user_message(self, text: str) -> None:
        if text.strip():
            self._append_node(kind="user", text=text)

    def append_assistant_text(self, text: str) -> None:
        if text.strip():
            self._append_mergeable_text(kind="assistant_text", text=text)

    def append_assistant_reasoning(self, text: str) -> None:
        if text.strip():
            self._append_mergeable_text(kind="assistant_reasoning", text=text)

    def begin_tool_call(self, *, name: str, call_id: str, args_str: str) -> None:
        node = self._append_node(
            kind="tool_call",
            name=name,
            call_id=call_id,
            args_str=args_str,
        )
        self.call_id_to_node_id[call_id] = node.id
        pending = self._pending_tool_results.pop(call_id, None)
        if pending is not None:
            node.name = pending.tool_name or node.name
            node.presentation = pending.presentation

    def complete_tool_result(
        self,
        *,
        tool_name: str,
        call_id: str | None,
        presentation: ToolPresentationModel,
    ) -> None:
        if call_id is None:
            self._append_node(
                kind="tool_call",
                name=tool_name,
                args_str="",
                presentation=presentation,
            )
            return

        node = self._find_tool_node(call_id)
        if node is None:
            self._pending_tool_results[call_id] = _PendingToolResult(
                tool_name=tool_name,
                presentation=presentation,
            )
            return

        node.name = tool_name or node.name
        node.presentation = presentation

    def begin_subagent_scope(self) -> None:
        node = self._append_node(kind="subagent_scope", expanded=False)
        self._scope_stack.append(node.id)

    def end_subagent_scope(self) -> None:
        if self._scope_stack:
            self._scope_stack.pop()

    def append_compaction_summary(
        self,
        *,
        total_tokens: int,
        max_tokens: int,
        strategy: str,
        remaining: int,
        current_tokens: int,
        summary_text: str | None,
    ) -> None:
        self._append_node(
            kind="compact_summary",
            usage={
                "total_tokens": total_tokens,
                "max_tokens": max_tokens,
                "remaining": remaining,
                "current_tokens": current_tokens,
            },
            text=summary_text or "",
            name=strategy,
        )

    def set_expanded(self, node_id: str, expanded: bool) -> None:
        node = self.nodes.get(node_id)
        if node is not None:
            node.expanded = expanded

    def get_node(self, node_id: str) -> TranscriptNode | None:
        return self.nodes.get(node_id)

    def latest_window(self, size: int) -> tuple[int, int]:
        end = len(self.top_level_ids)
        start = max(0, end - size)
        return start, end

    def previous_window(
        self,
        *,
        start: int,
        size: int,
        page_size: int,
    ) -> tuple[int, int]:
        new_start = max(0, start - page_size)
        return new_start, min(len(self.top_level_ids), new_start + size)

    def _find_tool_node(self, call_id: str) -> TranscriptNode | None:
        node_id = self.call_id_to_node_id.get(call_id)
        return self.nodes.get(node_id) if node_id is not None else None

    def _append_mergeable_text(self, *, kind: str, text: str) -> None:
        parent_id = self._scope_stack[-1] if self._scope_stack else None
        container = self.top_level_ids if parent_id is None else self.nodes[parent_id].children
        previous = self.nodes[container[-1]] if container else None
        if (
            previous is not None
            and previous.kind == kind
            and previous.parent_id == parent_id
            and previous.turn_id == self._current_turn_id
        ):
            previous.text = f"{previous.text}\n\n{text}" if previous.text.strip() else text
            return

        self._append_node(kind=kind, text=text)

    def _append_node(
        self,
        *,
        kind: str,
        text: str = "",
        usage: dict | None = None,
        name: str | None = None,
        call_id: str | None = None,
        args_str: str | None = None,
        presentation: ToolPresentationModel | None = None,
        expanded: bool = False,
    ) -> TranscriptNode:
        node_id = f"node_{next(self._id_counter)}"
        parent_id = self._scope_stack[-1] if self._scope_stack else None
        node = TranscriptNode(
            id=node_id,
            kind=kind,
            parent_id=parent_id,
            text=text,
            usage=usage,
            name=name,
            call_id=call_id,
            args_str=args_str,
            presentation=presentation,
            expanded=expanded,
            turn_id=self._current_turn_id if kind.startswith("assistant_") or kind == "tool_call" else None,
        )
        self.nodes[node_id] = node
        if parent_id is None:
            self.top_level_ids.append(node_id)
        else:
            self.nodes[parent_id].children.append(node_id)
        return node


__all__ = [
    "AssistantTurnState",
    "TranscriptNode",
    "TranscriptStore",
]
