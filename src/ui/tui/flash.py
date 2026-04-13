"""Flash notification widget — adapted from toad's Flash pattern.

Shows a timed, auto-dismissing notification bar above the input.
Styled with semantic colors (info/success/warning/error).
"""

from __future__ import annotations

from textual.reactive import var
from textual.timer import Timer
from textual.widgets import Static


class Flash(Static):
    """A timed notification bar that auto-dismisses after a duration.

    Usage:
        flash_widget.flash("Session saved", style="success")
        flash_widget.flash("Warning: ...", style="warning", duration=5.0)
    """

    DEFAULT_CSS = """
    Flash {
        height: 1;
        width: 1fr;
        text-align: center;
        display: none;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    Flash.-info {
        display: block;
        background: $primary 15%;
        color: $text;
    }
    Flash.-success {
        display: block;
        background: $success 15%;
        color: $text;
    }
    Flash.-warning {
        display: block;
        background: $warning 20%;
        color: $text;
    }
    Flash.-error {
        display: block;
        background: $error 20%;
        color: $text;
    }
    """

    _flash_timer: var[Timer | None] = var(None)

    def flash(
        self,
        content: str,
        *,
        duration: float = 3.0,
        style: str = "info",
    ) -> None:
        """Show a flash notification.

        Args:
            content: Message text to display.
            duration: Seconds before auto-dismiss.
            style: One of "info", "success", "warning", "error".
        """
        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer = None

        self.remove_class("-info", "-success", "-warning", "-error")
        self.update(content)
        self.add_class(f"-{style}")
        self._flash_timer = self.set_timer(duration, self._hide)

    def _hide(self) -> None:
        self.remove_class("-info", "-success", "-warning", "-error")
        self._flash_timer = None
