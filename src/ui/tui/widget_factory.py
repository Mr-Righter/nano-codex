"""Widget construction helpers for transcript nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ui.events import ToolPresentationModel

from .widgets.agent_message import AgentMessage
from .widgets.agent_thinking import AgentThinking
from .widgets.compact_summary import CompactSummary
from .widgets.subagent_block import SubagentBlock
from .widgets.tool_call_block import ToolCallBlock

if TYPE_CHECKING:
    from textual.widget import Widget

    from .transcript_store import TranscriptStore


class TranscriptWidgetFactory:
    """Map transcript nodes to concrete Textual widgets."""

    def __init__(self, store: "TranscriptStore") -> None:
        self._store = store

    def build(self, node_id: str) -> "Widget | None":
        node = self._store.get_node(node_id)
        if node is None:
            return None

        match node.kind:
            case "user":
                from .widgets.user_message import UserMessage

                return UserMessage(node.text)
            case "assistant_text":
                return AgentMessage(node.text)
            case "assistant_reasoning":
                return AgentThinking(node.text)
            case "tool_call":
                return self._build_tool_call(node_id)
            case "compact_summary":
                usage = node.usage or {}
                return CompactSummary(
                    usage.get("total_tokens", 0),
                    usage.get("current_tokens", 0),
                    node.text or None,
                    expanded=node.expanded,
                    on_toggle=lambda expanded, node_id=node.id: self._store.set_expanded(node_id, expanded),
                )
            case "subagent_scope":
                child_widgets: list[tuple[str, Widget]] = []
                for child_id in node.children:
                    child_widget = self.build(child_id)
                    if child_widget is not None:
                        child_widgets.append((child_id, child_widget))
                block = SubagentBlock(
                    initial_children=child_widgets,
                    expanded=node.expanded,
                    on_toggle=lambda expanded, node_id=node.id: self._store.set_expanded(node_id, expanded),
                )
                return block
            case _:
                return None

    def sync(self, widget: "Widget", node_id: str) -> bool:
        node = self._store.get_node(node_id)
        if node is None:
            return False

        match node.kind:
            case "assistant_text" if isinstance(widget, AgentMessage):
                widget.set_text(node.text)
                return True
            case "assistant_reasoning" if isinstance(widget, AgentThinking):
                widget.set_text(node.text)
                return True
            case "tool_call" if isinstance(widget, ToolCallBlock):
                widget.expanded = node.expanded
                diff_data = _presentation_diff_data(node.presentation)
                if diff_data is not None:
                    widget.set_diff(*diff_data)
                else:
                    result_text = _presentation_result_text(node.presentation)
                    if result_text is not None:
                        widget.set_result(result_text)
                return True
            case "subagent_scope" if isinstance(widget, SubagentBlock):
                widget.expanded = node.expanded
                return widget.sync_children(
                    node.children,
                    build_widget=self.build,
                    sync_widget=self.sync,
                )
            case "compact_summary" if isinstance(widget, CompactSummary):
                widget.expanded = node.expanded
                return True
            case "user":
                return True
            case _:
                return False

    def _build_tool_call(self, node_id: str) -> ToolCallBlock:
        node = self._store.get_node(node_id)
        assert node is not None
        presentation = node.presentation
        result_text = _presentation_result_text(presentation)
        diff_data = _presentation_diff_data(presentation)
        return ToolCallBlock(
            node.name or "tool",
            node.call_id,
            node.args_str or "",
            result_text=result_text,
            diff_data=diff_data,
            expanded=node.expanded,
            on_toggle=lambda expanded, node_id=node.id: self._store.set_expanded(node_id, expanded),
        )


def _presentation_result_text(presentation: ToolPresentationModel | None) -> str | None:
    """Flatten a presentation model into the text body shown in a tool block."""
    if presentation is None:
        return None

    if presentation.kind == "diff":
        return presentation.summary

    parts: list[str] = []
    if presentation.summary:
        parts.append(presentation.summary)

    if presentation.kind == "media_ref" and presentation.path:
        path_line = f"Path: {presentation.path}"
        if presentation.media_type:
            path_line += f" ({presentation.media_type})"
        if path_line not in parts:
            parts.append(path_line)

    if presentation.body and presentation.body != presentation.summary:
        parts.append(presentation.body)

    body = "\n\n".join(part for part in parts if part)
    return body or None


def _presentation_diff_data(
    presentation: ToolPresentationModel | None,
) -> tuple[str, str, str] | None:
    """Extract diff payload data when a presentation represents a file edit."""
    if presentation is None or presentation.kind != "diff":
        return None
    if presentation.path is None:
        return None
    return (
        presentation.path,
        presentation.old_text or "",
        presentation.new_text or "",
    )


__all__ = ["TranscriptWidgetFactory"]
