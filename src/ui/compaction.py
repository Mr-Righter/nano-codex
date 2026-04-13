"""UI helpers for compaction-related runtime callbacks and events."""

from __future__ import annotations

from collections.abc import Callable

from src.ui.events import CompactionSummaryEvent
from src.ui.protocol import UiEventSink
from src.utils.auto_compact import CompactionOutcome


def emit_compaction_summary(
    ui_sink: UiEventSink,
    outcome: CompactionOutcome,
) -> None:
    """Emit one compaction summary event when the outcome actually compacted history."""
    if not outcome.was_compacted:
        return
    ui_sink.emit(
        CompactionSummaryEvent(
            total_tokens=outcome.total_tokens,
            max_tokens=outcome.max_tokens,
            strategy=outcome.strategy,
            remaining=outcome.remaining,
            current_tokens=outcome.current_tokens,
            summary_text=outcome.summary_text,
        )
    )


def build_compaction_ui_callback(
    ui_sink: UiEventSink | None,
) -> Callable[[CompactionOutcome], None] | None:
    """Build the optional callback that converts compaction outcomes into UI events."""
    if ui_sink is None:
        return None

    def callback(outcome: CompactionOutcome) -> None:
        emit_compaction_summary(ui_sink, outcome)

    return callback


__all__ = ["build_compaction_ui_callback", "emit_compaction_summary"]
