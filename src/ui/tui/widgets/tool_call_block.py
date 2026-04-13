"""Collapsible widget for tool-call arguments, results, and diffs."""

from __future__ import annotations

import json

from rich.text import Text
from collections.abc import Callable

from textual import containers, events, on
from textual.app import ComposeResult
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import Static


class ToolCallBlock(containers.VerticalGroup):
    """Collapsible tool call widget. Toad ToolCall pattern.

    Shows a clickable header (▶/▼ 🔧 name  [call_id]).
    Click to expand/collapse args and result.
    Call set_result() when the tool response arrives.
    Call set_diff() for edit/write tool responses (embeds a DiffBlock).
    Result-only tool events may omit ``call_id`` and arguments.
    """

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    ToolCallBlock {
        margin: 0 1 1 1;
        layout: stream;
        height: auto;
        border-left: solid $success;
        padding-left: 1;
    }
    ToolCallBlock #header {
        pointer: pointer;
        width: 1fr;
        color: $success;
    }
    ToolCallBlock #header:hover {
        background: $panel;
    }
    ToolCallBlock #tool-content {
        display: none;
        padding: 0 0 0 2;
    }
    ToolCallBlock.-expanded #tool-content {
        display: block;
    }
    ToolCallBlock #args {
        background: $panel;
        color: $text-muted;
        padding: 0 1;
        margin-bottom: 1;
    }
    ToolCallBlock #result {
        border-top: dashed $panel;
        padding-top: 1;
        margin-top: 0;
        color: $text;
        background: $panel;
        padding: 1;
    }
    ToolCallBlock #result-pending {
        color: $text-muted;
        text-style: dim italic;
        padding: 0 1;
    }
    """

    expanded: var[bool] = var(False, toggle_class="-expanded")

    def __init__(
        self,
        name: str,
        call_id: str | None,
        args_str: str,
        *,
        result_text: str | None = None,
        diff_data: tuple[str, str, str] | None = None,
        expanded: bool = False,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self._name = name
        self._call_id = call_id
        self._args_str = args_str
        self._result_text = result_text
        self._diff_data = diff_data
        self._has_diff = diff_data is not None
        self._on_toggle = on_toggle
        self.expanded = expanded

    def _header_text(self) -> Text:
        symbol = "▼" if self.expanded else "▶"
        status = " ✔" if self._result_text is not None else " ⌛"
        t = Text(no_wrap=True)
        t.append(f"{symbol} 🔧 {self._name}{status}")
        if self._call_id:
            t.append("  [")
            t.append(self._call_id)
            t.append("]")
        return t

    def _formatted_args(self) -> str:
        if not self._args_str.strip():
            return "(no arguments)"
        try:
            parsed = json.loads(self._args_str)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            return self._args_str

    def _format_result(self) -> Text:
        """Format result text as plain Rich Text (no markup parsing)."""
        if self._result_text is None:
            return Text("")
        text = self._result_text
        lines = text.splitlines()
        max_lines = 200
        if len(lines) > max_lines:
            truncated = len(lines) - max_lines
            text = "\n".join(lines[:max_lines]) + f"\n... ({truncated} lines truncated)"
        return Text(text)

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="header")
        with containers.VerticalGroup(id="tool-content"):
            yield Static(Text(self._formatted_args()), id="args")
            with containers.VerticalGroup(id="result-slot"):
                yield self._build_result_widget()

    def _build_result_widget(self) -> Widget:
        if not self._has_diff:
            if self._result_text is not None:
                return Static(self._format_result(), id="result")
            return Static("waiting for result...", id="result-pending")

        assert self._diff_data is not None
        from src.ui.tui.widgets.diff_block import DiffBlock

        path, old_text, new_text = self._diff_data
        return DiffBlock(path, old_text, new_text)

    def _sync_result_widget(self) -> None:
        if not self.is_mounted:
            return

        try:
            self.query_one("#header", Static).update(self._header_text())
            slot = self.query_one("#result-slot", containers.VerticalGroup)
        except Exception:
            return

        for child in list(slot.children):
            child.remove()
        slot.mount(self._build_result_widget())

    def set_result(self, result: str) -> None:
        """Update widget with tool response text. Called on main thread."""
        if result == self._result_text and not self._has_diff:
            return
        self._result_text = result
        self._has_diff = False
        self._diff_data = None
        self._sync_result_widget()

    def set_diff(self, path: str, old_text: str, new_text: str) -> None:
        """Update widget with a diff view. Called on main thread."""
        import difflib

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        result_text = f"✔  {path}  (+{added} -{removed})"
        diff_data = (path, old_text, new_text)
        if self._has_diff and self._diff_data == diff_data and self._result_text == result_text:
            return

        self._result_text = result_text
        self._has_diff = True
        self._diff_data = diff_data
        self._sync_result_widget()

    def watch_expanded(self) -> None:
        try:
            self.query_one("#header", Static).update(self._header_text())
        except Exception:
            pass

    @on(events.Click, "#header")
    def toggle_expand(self, event: events.Click) -> None:
        event.stop()
        self.expanded = not self.expanded
        if self._on_toggle is not None:
            self._on_toggle(self.expanded)
