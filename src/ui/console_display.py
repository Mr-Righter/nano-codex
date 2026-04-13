"""Rich console renderer for single-task and fallback interactive modes."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme
from .events import (
    AssistantReasoningEvent,
    AssistantTextEvent,
    AssistantTurnStarted,
    CompactionSummaryEvent,
    InfoNotice,
    SessionEnded,
    SessionRestored,
    SessionSaved,
    SessionStarted,
    SubagentScopeEnded,
    SubagentScopeStarted,
    ToolCallStarted,
    ToolPresentationModel,
    ToolResultEvent,
    UiEvent,
    UserMessageEvent,
    WarningNotice,
)


THEME = Theme(
    {
        "border": "bright_black",
        "border.accent": "rgb(63,128,190)",
        "header.agent": "bold bright_white",
        "header.tool": "bold green",
        "header.user": "bold rgb(63,128,190)",
        "header.system": "bold yellow",
        "thinking": "dim italic",
        "tool.name": "rgb(63,128,190)",
        "tool.callid": "dim",
        "tool.args": "white",
        "token.label": "dim",
        "token.value": "dim bright_white",
        "status.info": "rgb(63,128,190)",
        "status.warn": "yellow",
        "status.error": "red",
        "status.success": "green",
        "subagent": "bold magenta",
        "subagent.border": "magenta",
    }
)


def _build_token_text(usage: dict | None) -> str:
    """Build a compact token usage string from usage_details dict."""
    if not usage:
        return ""

    parts: list[str] = []
    if usage.get("input_token_count") is not None:
        parts.append(f"in:{usage['input_token_count']:,}")
    if usage.get("output_token_count") is not None:
        parts.append(f"out:{usage['output_token_count']:,}")
    return "  ".join(parts)


def _build_tool_args_renderable(args_str: str) -> object:
    """Build a Rich renderable for one tool-call argument payload."""
    renderables: list[object] = []

    truncated = False
    max_chars = 800
    if len(args_str) > max_chars:
        args_str = args_str[:max_chars]
        truncated = True

    try:
        parsed = json.loads(args_str)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        renderables.append(
            Syntax(
                formatted,
                "json",
                theme="monokai",
                word_wrap=True,
                padding=(0, 2),
            )
        )
    except (json.JSONDecodeError, ValueError):
        renderables.append(Text(args_str, style="tool.args"))

    if truncated:
        renderables.append(Text(f"... truncated to {max_chars} chars", style="dim"))

    if len(renderables) == 1:
        return renderables[0]
    return Group(*renderables)


@dataclass(frozen=True)
class _PendingToolCall:
    name: str
    args_str: str
    depth: int


class RichConsoleDisplay:
    """Display implementation using Rich Console for single-task mode."""

    def __init__(self) -> None:
        self.console = Console(theme=THEME, highlight=False)
        self._pending_tool_calls: dict[str, _PendingToolCall] = {}
        self._subagent_depth = 0

    def emit(self, event: UiEvent) -> None:
        match event:
            case AssistantTurnStarted(usage=usage):
                self._show_response_header(usage)
            case AssistantReasoningEvent(text=text):
                self._show_thinking(text)
            case ToolCallStarted(name=name, call_id=call_id, args_str=args_str):
                self._show_tool_call(name, call_id, args_str)
            case AssistantTextEvent(text=text):
                self._show_response_text(text)
            case ToolResultEvent(tool_name=tool_name, call_id=call_id, presentation=presentation):
                self._show_tool_result(tool_name, call_id, presentation)
            case SubagentScopeStarted():
                self._show_subagent_enter()
            case SubagentScopeEnded():
                self._show_subagent_exit()
            case CompactionSummaryEvent() as compact:
                self._show_compact_summary(
                    compact.total_tokens,
                    compact.max_tokens,
                    compact.strategy,
                    compact.remaining,
                    compact.summary_text,
                )
            case SessionStarted():
                self._show_session_start()
            case SessionEnded():
                self._show_session_end()
            case SessionRestored(path=path):
                self._show_session_restored(path)
            case SessionSaved(path=path):
                self._show_session_saved(path)
            case InfoNotice(text=text):
                self._show_info(text)
            case WarningNotice(text=text):
                self._show_warning(text)
            case UserMessageEvent():
                pass
            case _:
                pass

    def _show_response_header(self, usage: dict | None) -> None:
        self._flush_pending_tool_calls()
        token_info = _build_token_text(usage)
        title = "[header.agent]Assistant[/header.agent]"
        if token_info:
            title += f"  [token.label]{token_info}[/token.label]"
        self._print_block(Rule(title=title, style="border", align="left"))

    def _show_thinking(self, text: str) -> None:
        self._flush_pending_tool_calls()
        title = Text("Thinking", style="header.system")
        body = Text(text, style="thinking")
        self._print_panel(title=title, body=body, border_style="border")

    def _show_tool_call(self, name: str, call_id: str, args_str: str) -> None:
        self._pending_tool_calls[call_id] = _PendingToolCall(
            name=name,
            args_str=args_str,
            depth=self._subagent_depth,
        )

    def _show_response_text(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Assistant Output", style="header.agent"),
            body=Markdown(text),
            border_style="border",
        )

    def _show_tool_result(
        self,
        tool_name: str,
        call_id: str | None,
        presentation: ToolPresentationModel,
    ) -> None:
        pending = self._pending_tool_calls.pop(call_id, None) if call_id else None
        matched_name = pending.name if pending is not None else tool_name
        panel_depth = pending.depth if pending is not None else self._subagent_depth

        if presentation.kind == "diff":
            self._show_edit_result(matched_name, call_id, presentation, pending=pending)
            return

        title = self._make_tool_title(matched_name, call_id)
        body_items: list[object] = []
        if pending is not None:
            body_items.extend(self._build_tool_arguments_section(pending.args_str))
        body_items.extend(self._build_tool_result_section(presentation))
        self._print_panel(
            title=title,
            body=Group(*body_items),
            border_style="header.tool",
            subtitle="tool",
            depth=panel_depth,
        )

    def _format_result_details(self, presentation: ToolPresentationModel) -> str:
        parts: list[str] = []
        if presentation.kind == "media_ref" and presentation.path:
            parts.append(f"Path: {presentation.path}")
            if presentation.media_type:
                parts.append(f"Type: {presentation.media_type}")

        body = presentation.body or ""
        if body and body != presentation.summary:
            parts.append(body)

        return _truncate_result_text("\n".join(part for part in parts if part))

    def _show_subagent_enter(self) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Subagent", style="subagent"),
            body=Text("Entering delegated execution", style="subagent"),
            border_style="subagent.border",
            depth=self._subagent_depth,
        )
        self._subagent_depth += 1

    def _show_subagent_exit(self) -> None:
        self._flush_pending_tool_calls()
        self._subagent_depth = max(0, self._subagent_depth - 1)
        self._print_panel(
            title=Text("Subagent", style="subagent"),
            body=Text("Delegated execution finished", style="subagent"),
            border_style="subagent.border",
            depth=self._subagent_depth,
        )

    def _show_compact_summary(
        self,
        total_tokens: int,
        max_tokens: int,
        strategy: str,
        remaining: int,
        summary_text: str | None,
    ) -> None:
        self._flush_pending_tool_calls()
        info = Text()
        info.append(f"Tokens: {total_tokens:,} / {max_tokens:,}", style="header.system")
        info.append(f"  Strategy: {strategy}", style="header.system")
        info.append(f"  Remaining: {remaining} msg(s)", style="header.system")

        body_items: list[object] = [info]
        if summary_text:
            body_items.append(Text(summary_text))
        self._print_panel(
            title=Text("Auto-Compact", style="header.system"),
            body=Group(*body_items),
            border_style="header.system",
        )

    def _show_session_start(self) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Session", style="header.system"),
            body=Text("Nano-Codex interactive session started", style="status.info"),
            border_style="border",
        )

    def _show_session_end(self) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Session", style="header.system"),
            body=Text("Session ended. Goodbye.", style="status.info"),
            border_style="border",
        )

    def _show_session_restored(self, path) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Session", style="header.system"),
            body=Text(f"Resumed previous session from {path}", style="status.info"),
            border_style="border",
        )

    def _show_session_saved(self, path) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Session", style="header.system"),
            body=Text(f"Session saved to {path}", style="status.info"),
            border_style="border",
        )

    def _show_info(self, text: str) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Info", style="header.system"),
            body=Text(text, style="status.info"),
            border_style="border",
        )

    def _show_warning(self, text: str) -> None:
        self._flush_pending_tool_calls()
        self._print_panel(
            title=Text("Warning", style="status.warn"),
            body=Text(text, style="status.warn"),
            border_style="status.warn",
        )

    def _show_edit_result(
        self,
        tool_name: str,
        call_id: str | None,
        presentation: ToolPresentationModel,
        *,
        pending: _PendingToolCall | None,
    ) -> None:
        path = presentation.path or "<unknown>"
        old_text = presentation.old_text or ""
        new_text = presentation.new_text or ""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        title = self._make_tool_title(tool_name, call_id)
        body_items: list[object] = []
        if pending is not None:
            body_items.extend(self._build_tool_arguments_section(pending.args_str))
        body_items.extend(self._build_tool_result_section(presentation))
        if diff:
            added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
            summary = Text()
            summary.append(f"{Path(path).name}: ", style="bold")
            summary.append(f"+{added}", style="green")
            summary.append(" / ")
            summary.append(f"-{removed}", style="red")
            body_items.append(summary)
        self._print_panel(
            title=title,
            body=Group(*body_items),
            border_style="header.tool",
            subtitle="tool",
            depth=pending.depth if pending is not None else self._subagent_depth,
        )

    def _build_tool_arguments_section(self, args_str: str) -> list[object]:
        return [
            Text("Arguments", style="header.system"),
            _build_tool_args_renderable(args_str),
        ]

    def _build_tool_result_section(self, presentation: ToolPresentationModel) -> list[object]:
        body_items: list[object] = [
            Text("Result", style="header.system"),
            Text(presentation.summary, style="header.tool"),
        ]
        details = self._format_result_details(presentation)
        if details:
            body_items.append(Text(details))
        return body_items

    def _flush_pending_tool_calls(self) -> None:
        if not self._pending_tool_calls:
            return

        pending_items = list(self._pending_tool_calls.items())
        self._pending_tool_calls.clear()
        for call_id, pending in pending_items:
            body = Group(
                *self._build_tool_arguments_section(pending.args_str),
                Text("Result", style="header.system"),
                Text("No result received before output moved on.", style="status.warn"),
            )
            self._print_panel(
                title=self._make_tool_title(pending.name, call_id),
                body=body,
                border_style="status.warn",
                subtitle="tool",
                depth=pending.depth,
            )

    def _make_tool_title(self, tool_name: str, call_id: str | None) -> Text:
        title = Text()
        title.append("Tool", style="header.tool")
        title.append("  ")
        title.append(tool_name, style="tool.name")
        if call_id:
            title.append("  ")
            title.append(f"[{call_id}]", style="tool.callid")
        return title

    def _print_panel(
        self,
        *,
        title: str | Text,
        body: object,
        border_style: str,
        subtitle: str | None = None,
        depth: int | None = None,
    ) -> None:
        self._print_block(
            Panel(
                body,
                title=title,
                subtitle=subtitle,
                title_align="left",
                subtitle_align="left",
                border_style=border_style,
                box=box.ROUNDED,
                padding=(0, 1),
                expand=True,
            ),
            depth=depth,
        )

    def _print_block(self, renderable: object, *, depth: int | None = None) -> None:
        left_padding = max((self._subagent_depth if depth is None else depth) * 2, 0)
        if left_padding:
            renderable = Padding(renderable, (0, 0, 0, left_padding))
        self.console.print()
        self.console.print(renderable)


def _truncate_result_text(text: str, *, max_chars: int = 1000) -> str:
    """Clamp long console tool-result bodies to a readable size."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n... truncated to {max_chars} chars"


__all__ = ["RichConsoleDisplay"]
