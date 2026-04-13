"""Collapsible widget that displays auto-compaction summaries in the transcript."""

from __future__ import annotations

from collections.abc import Callable

from textual import containers, events, on
from textual.app import ComposeResult
from textual.reactive import var
from textual.widgets import Markdown, Static


class CompactSummary(containers.VerticalGroup):
    """Collapsible compaction summary widget."""

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    CompactSummary {
        margin: 1 1;
        border-left: solid $warning;
        padding-left: 1;
        background: $warning 6%;
    }
    CompactSummary #compact-header {
        pointer: pointer;
        color: $warning;
        text-style: bold;
    }
    CompactSummary #compact-header:hover {
        background: $panel;
    }
    CompactSummary #compact-content {
        display: none;
        padding: 0 0 0 2;
    }
    CompactSummary.-expanded #compact-content {
        display: block;
    }
    CompactSummary Markdown {
        margin: 0;
        padding: 0;
        background: transparent;
    }
    """

    expanded: var[bool] = var(False, toggle_class="-expanded")

    def __init__(
        self,
        total_tokens: int,
        current_tokens: int,
        summary_text: str | None,
        *,
        expanded: bool = False,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self._total_tokens = total_tokens
        self._current_tokens = current_tokens
        self._summary_text = summary_text or "_No summary available._"
        self._on_toggle = on_toggle
        self.expanded = expanded

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="compact-header")
        with containers.VerticalGroup(id="compact-content"):
            yield Markdown(self._summary_text)

    def _header_text(self) -> str:
        symbol = "▼" if self.expanded else "▶"
        return (
            f"{symbol} Summarization - before: {self._total_tokens:,} tokens, "
            f"after: {self._current_tokens:,} tokens"
        )

    def watch_expanded(self) -> None:
        try:
            self.query_one("#compact-header", Static).update(self._header_text())
        except Exception:
            pass

    @on(events.Click, "#compact-header")
    def toggle(self, event: events.Click) -> None:
        event.stop()
        self.expanded = not self.expanded
        if self._on_toggle is not None:
            self._on_toggle(self.expanded)
