"""Public UI contracts, events, and lightweight runtime wiring for Nano-Codex."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .console_display import RichConsoleDisplay
from .events import (
    AssistantReasoningEvent,
    AssistantTextEvent,
    AssistantTurnStarted,
    CompactionSummaryEvent,
    InfoNotice,
    SessionEnded,
    SessionRestored,
    SessionSaved,
    SessionStarted,
    SubagentScopeEnded,
    SubagentScopeStarted,
    ToolCallStarted,
    ToolPresentationModel,
    ToolResultEvent,
    UiEvent,
    UserMessageEvent,
    WarningNotice,
)
from .protocol import (
    NULL_UI_RUNTIME,
    NullUiEventSink,
    UiControlPort,
    UiEventSink,
    UiRuntime,
)

if TYPE_CHECKING:
    from .tui.app import NanoCodexApp


UiMode = Literal["console", "tui"]


def create_ui_runtime(
    mode: UiMode,
    *,
    app: "NanoCodexApp | None" = None,
    window_size: int = 120,
) -> UiRuntime:
    """Create the runtime wiring for the requested UI mode."""
    if mode == "console":
        sink = RichConsoleDisplay()
        return UiRuntime(sink=sink)

    if mode == "tui":
        if app is None:
            raise ValueError("app is required when mode='tui'")

        from .tui.display import TextualDisplay

        display = TextualDisplay(app, window_size=window_size)
        return UiRuntime(sink=display, controls=display)

    raise ValueError(f"Unsupported UI mode: {mode}")

__all__ = [
    "AssistantReasoningEvent",
    "AssistantTextEvent",
    "AssistantTurnStarted",
    "CompactionSummaryEvent",
    "InfoNotice",
    "NULL_UI_RUNTIME",
    "NullUiEventSink",
    "RichConsoleDisplay",
    "SessionEnded",
    "SessionRestored",
    "SessionSaved",
    "SessionStarted",
    "SubagentScopeEnded",
    "SubagentScopeStarted",
    "ToolCallStarted",
    "ToolPresentationModel",
    "ToolResultEvent",
    "UiControlPort",
    "UiEvent",
    "UiEventSink",
    "UiMode",
    "UiRuntime",
    "UserMessageEvent",
    "WarningNotice",
    "create_ui_runtime",
]
