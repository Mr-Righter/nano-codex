"""SlashComplete — autocomplete overlay for slash commands.

Adapted from toad's SlashComplete widget. Displays a fuzzy-searchable list of
slash commands above the input area when the user types "/".
"""

from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter
from typing import Iterable, Sequence

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content, Span
from textual.message import Message
from textual.reactive import var
from textual import containers
from textual import widgets
from textual.widget import Widget
from textual.widgets.option_list import Option

from src.ui.tui.fuzzy import FuzzySearch
from src.ui.tui.slash_command import SlashCommand


class Dismiss(Message):
    """Posted by SlashComplete when it wants to be hidden (escape or blur)."""

    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self.widget = widget


class SlashComplete(containers.VerticalGroup):
    """Fuzzy-searchable autocomplete overlay for slash commands.

    Shows a filterable list of available slash commands. Posts
    ``SlashComplete.Completed(command)`` when user selects an entry.
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "submit", "Select", priority=True),
        Binding("escape", "dismiss", "Dismiss", priority=True),
    ]

    slash_commands: var[list[SlashCommand]] = var(list)

    @dataclass
    class Completed(Message):
        """Posted when the user selects a slash command."""
        command: str  # e.g. "/compact"

    def __init__(
        self,
        slash_commands: Iterable[SlashCommand] | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.slash_commands = list(slash_commands) if slash_commands else []
        self.fuzzy_search = FuzzySearch(case_sensitive=False)

    def compose(self) -> ComposeResult:
        yield widgets.Input(placeholder="filter commands…", id="slash-filter-input")
        yield widgets.OptionList(id="slash-option-list")

    def on_mount(self) -> None:
        self.filter_slash_commands("")

    def focus(self, scroll_visible: bool = False):  # type: ignore[override]
        self.filter_slash_commands(self.query_one("#slash-filter-input", widgets.Input).value)
        self.query_one("#slash-filter-input", widgets.Input).focus(scroll_visible)
        return self

    def on_descendant_blur(self) -> None:
        # Hide if focus leaves the entire widget subtree
        self.post_message(Dismiss(self))

    @on(widgets.Input.Changed)
    def on_input_changed(self, event: widgets.Input.Changed) -> None:
        event.stop()
        self.filter_slash_commands(event.value)

    async def watch_slash_commands(self, slash_commands: list[SlashCommand]) -> None:
        self.filter_slash_commands(
            self.query_one("#slash-filter-input", widgets.Input).value
        )

    def filter_slash_commands(self, prompt: str) -> None:
        """Re-render the OptionList filtered by *prompt*."""
        prompt = prompt.lstrip("/").casefold().rstrip()

        slash_commands = sorted(
            self.slash_commands,
            key=lambda c: c.command.casefold(),
        )
        deduplicated = {c.command: c for c in slash_commands}
        self.fuzzy_search.cache.grow(len(deduplicated))

        if prompt:
            slash_prompt = f"/{prompt}"
            scores: list[tuple[float, Sequence[int], SlashCommand]] = [
                (
                    *self.fuzzy_search.match(prompt, cmd.command[1:]),
                    cmd,
                )
                for cmd in slash_commands
            ]
            scores = sorted(
                [
                    (
                        score * 2 if cmd.command.casefold().startswith(slash_prompt) else score,
                        highlights,
                        cmd,
                    )
                    for score, highlights, cmd in scores
                    if score
                ],
                key=itemgetter(0),
                reverse=True,
            )
        else:
            scores = [(1.0, [], cmd) for cmd in slash_commands]

        def make_option(cmd: SlashCommand, indices: Iterable[int]) -> Content:
            """Render one option row: colored command + dim help text, truncated to one line."""
            # Popup inner width: 72 (total) - 2 (border) - 4 (margin+padding each side) = 66
            # Reserve 2 chars for separator "  "
            _POPUP_INNER = 66
            max_help = max(0, _POPUP_INNER - len(cmd.command) - 2)
            help_text = cmd.help if len(cmd.help) <= max_help else cmd.help[: max_help - 1] + "…"
            command_content = Content.styled(cmd.command, "rgb(63,128,190)")
            command_content = command_content.add_spans(
                [Span(i + 1, i + 2, "underline not dim") for i in indices]
            )
            return Content.assemble(command_content, "  ", (help_text, "dim"))

        option_list = self.query_one("#slash-option-list", widgets.OptionList)
        option_list.set_options(
            Option(make_option(cmd, indices), id=cmd.command)
            for _, indices, cmd in scores
        )
        # Highlight first entry
        if self.display:
            option_list.highlighted = 0
        else:
            with option_list.prevent(widgets.OptionList.OptionHighlighted):
                option_list.highlighted = 0

    def action_cursor_up(self) -> None:
        self.query_one("#slash-option-list", widgets.OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#slash-option-list", widgets.OptionList).action_cursor_down()

    def action_dismiss(self) -> None:
        self.post_message(Dismiss(self))

    def action_submit(self) -> None:
        option_list = self.query_one("#slash-option-list", widgets.OptionList)
        if (option := option_list.highlighted_option) is not None:
            # Clear the filter input silently before dismissing
            with self.query_one("#slash-filter-input", widgets.Input).prevent(widgets.Input.Changed):
                self.query_one("#slash-filter-input", widgets.Input).clear()
            self.post_message(self.Completed(option.id or ""))
