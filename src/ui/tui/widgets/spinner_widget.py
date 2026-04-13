"""Busy/ready status indicator used by the Nano-Codex Textual footer."""

from __future__ import annotations

import random
import time

from rich.spinner import Spinner
from rich.text import Text
from textual.widget import Widget

_SPINNER_NAMES = ["dots", "betaWave", "aesthetic", "line", "bouncingBall", "star"]

_WORKING_PHRASES = [
    "Cooking",
    "On it",
    "Grinding",
    "Vibing",
    "Crunching",
    "Brewing",
    "Conjuring",
    "Pondering",
    "Scheming",
    "Wrangling",
    "Churning",
    "Summoning",
]

# How many auto_refresh ticks before cycling to next phrase (~3s at 1/12s per tick)
_PHRASE_TICKS = 36


class SpinnerWidget(Widget):
    """Animated status widget: spinner + cycling text when busy, green dot + Ready when idle."""

    DEFAULT_CSS = """
    SpinnerWidget {
        width: auto;
        height: 1;
        background: transparent;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._busy = False
        self._spinner = Spinner("dots", style="yellow")
        self._phrases: list[str] = []
        self._phrase_idx = 0
        self._tick = 0

    def on_mount(self) -> None:
        self.auto_refresh = 1 / 12

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._spinner = Spinner(random.choice(_SPINNER_NAMES), style="yellow")
            self._phrases = random.sample(_WORKING_PHRASES, len(_WORKING_PHRASES))
            self._phrase_idx = 0
            self._tick = 0
        self.refresh()

    def render(self) -> Text:
        if not self._busy:
            return Text("● Ready", style="green")

        # Advance phrase every _PHRASE_TICKS ticks
        self._tick += 1
        if self._tick >= _PHRASE_TICKS:
            self._tick = 0
            self._phrase_idx = (self._phrase_idx + 1) % len(self._phrases)

        phrase = self._phrases[self._phrase_idx]
        dots_count = (self._tick // 4) % 4  # 0-3 cycling dots
        dots = "." * dots_count + " " * (3 - dots_count)

        spinner_text = self._spinner.render(time.time())
        result = Text()
        result.append_text(spinner_text)
        result.append(f" {phrase}{dots}", style="yellow")
        return result
