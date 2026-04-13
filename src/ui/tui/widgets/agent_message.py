"""Markdown widget for assistant response text blocks."""

from __future__ import annotations

from textual.widgets import Markdown


class AgentMessage(Markdown):
    """Agent response rendered as Markdown. Each response text block is a separate widget."""

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    AgentMessage {
        padding: 0;
        min-height: 1;
        layout: stream;
        margin: 0 1 1 1;
        border-left: solid rgb(63, 128, 190);
        padding-left: 1;
    }
    AgentMessage > MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    """

    def __init__(self, text: str) -> None:
        self._text = text
        super().__init__(text)

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        if self.is_mounted:
            self.update(self._text)

    def append_text(self, text: str) -> None:
        if not text.strip():
            return
        self._text = f"{self._text}\n\n{text}" if self._text.strip() else text
        if self.is_mounted:
            self.update(self._text)
