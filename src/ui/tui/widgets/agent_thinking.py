"""Widget for rendering assistant reasoning/thinking blocks."""

from __future__ import annotations

from textual import containers
from textual.app import ComposeResult
from textual.widgets import Label, Markdown


class AgentThinking(containers.VerticalGroup):
    """Agent thinking/reasoning block — amber toned, capped height. Toad AgentThought pattern."""

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    AgentThinking {
        background: transparent;
        margin: 0 1 1 0;
        padding: 0 1;
        layout: stream;
        border-left: solid $panel;
    }
    AgentThinking #thinking-header {
        color: $text-muted;
        text-style: italic;
    }
    AgentThinking #thinking-content {
        color: $text-muted;
        text-style: italic;
        padding: 0;
        background: transparent;
    }
    AgentThinking #thinking-content > MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        if self.is_mounted:
            self.query_one("#thinking-content", Markdown).update(self._text)

    def append_text(self, text: str) -> None:
        if not text.strip():
            return
        self._text = f"{self._text}\n\n{text}" if self._text.strip() else text
        if self.is_mounted:
            self.query_one("#thinking-content", Markdown).update(self._text)

    def compose(self) -> ComposeResult:
        yield Label("💭 Thinking", id="thinking-header")
        yield Markdown(self._text, id="thinking-content")
