"""Widget for rendering one user-authored chat message in the transcript."""

from __future__ import annotations

from textual import containers
from textual.app import ComposeResult
from textual.widgets import Label, Markdown


class UserMessage(containers.HorizontalGroup):
    """User input message with ❯ prefix and subtle background. Toad UserInput pattern."""

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    UserMessage {
        border-left: solid rgb(214, 143, 63);
        background: rgb(214, 143, 63) 12%;
        padding: 1 1 1 0;
        margin: 1 1 1 0;
    }
    UserMessage #prompt {
        margin: 0 1 0 0;
        color: rgb(214, 143, 63);
    }
    UserMessage Markdown {
        padding: 0 2 0 0;
        color: $text;
    }
    UserMessage Markdown > MarkdownBlock:last-child {
        margin-bottom: 0;
    }
    """

    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        yield Label("❯", id="prompt")
        yield Markdown(self._content, id="content")
