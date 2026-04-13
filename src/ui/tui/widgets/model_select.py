"""ModelSelect — overlay for switching the active LLM model.

Presents an OptionList of models from model_config.json. Posts
``ModelSelect.Completed(model)`` when the user confirms a selection.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual import containers
from textual import widgets
from textual.widget import Widget
from textual.widgets.option_list import Option


class ModelSelectDismiss(Message):
    """Posted by ModelSelect when it wants to be hidden (escape or blur)."""

    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self.widget = widget


class ModelSelect(containers.VerticalGroup):
    """Overlay for switching the active LLM model.

    Shows a scrollable list of available models. Posts
    ``ModelSelect.Completed(model)`` when the user selects an entry.
    """

    BINDINGS = [
        Binding("up",     "cursor_up",   "Up",      priority=True),
        Binding("down",   "cursor_down", "Down",    priority=True),
        Binding("enter",  "submit",      "Select",  priority=True),
        Binding("escape", "dismiss",     "Dismiss", priority=True),
    ]

    @dataclass
    class Completed(Message):
        """Posted when the user confirms a model selection."""
        model: str

    def compose(self) -> ComposeResult:
        yield widgets.OptionList(id="model-option-list")

    def populate(self, models: list[str], current: str | None = None) -> None:
        """Fill the OptionList with *models*, marking *current* with a checkmark."""
        option_list = self.query_one("#model-option-list", widgets.OptionList)
        option_list.clear_options()
        for name in models:
            label = f"{name}  ✓" if name == current else name
            option_list.add_option(Option(label, id=name))
        # Pre-highlight the current model so the user sees where they are
        if current and current in models:
            with option_list.prevent(widgets.OptionList.OptionHighlighted):
                option_list.highlighted = models.index(current)
        elif models:
            with option_list.prevent(widgets.OptionList.OptionHighlighted):
                option_list.highlighted = 0

    def focus(self, scroll_visible: bool = False) -> "ModelSelect":  # type: ignore[override]
        self.query_one("#model-option-list", widgets.OptionList).focus(scroll_visible)
        return self

    def on_descendant_blur(self) -> None:
        # Only dismiss if nothing inside this widget still has focus.
        # Use call_later to give Textual one event-loop tick to settle focus
        # (e.g. after populate() reassigns highlighted, or after show_model_select
        # reactive triggers a relayout that momentarily shifts focus).
        self.call_later(self._check_blur)

    def _check_blur(self) -> None:
        """Dismiss only if the overlay subtree genuinely lost focus."""
        if not self.query("*:focus"):
            self.post_message(ModelSelectDismiss(self))

    def action_cursor_up(self) -> None:
        self.query_one("#model-option-list", widgets.OptionList).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#model-option-list", widgets.OptionList).action_cursor_down()

    def action_dismiss(self) -> None:
        self.post_message(ModelSelectDismiss(self))

    def action_submit(self) -> None:
        option_list = self.query_one("#model-option-list", widgets.OptionList)
        if (option := option_list.highlighted_option) is not None:
            self.post_message(self.Completed(option.id or ""))
