"""Slash command definition for the Nano-Codex TUI.

Each slash command is a simple data record with a command name (e.g. "/compact")
and a help string shown in the autocomplete overlay.
"""

from __future__ import annotations

import rich.repr
from textual.content import Content


@rich.repr.auto
class SlashCommand:
    """A slash command definition shown in the autocomplete overlay."""

    def __init__(self, command: str, help: str) -> None:
        """
        Args:
            command: The full command string including leading slash, e.g. ``"/compact"``.
            help: One-line description shown next to the command in the overlay.
        """
        self.command = command
        self.help = help

    def __rich_repr__(self) -> rich.repr.Result:
        yield self.command
        yield "help", self.help

    def __str__(self) -> str:
        return self.command

    @property
    def content(self) -> Content:
        """Rich Content for rendering command + help in an OptionList."""
        return Content.assemble(
            (self.command, "rgb(63,128,190)"), "  ", (self.help, "dim")
        )
