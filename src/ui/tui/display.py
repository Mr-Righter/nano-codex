"""Textual UI runtime backed by a transcript store and windowed rendering."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.containers import VerticalScroll

from src.ui.events import (
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
    ToolResultEvent,
    UiEvent,
    UserMessageEvent,
    WarningNotice,
)
from src.ui.protocol import UiControlPort, UiEventSink

from .transcript_store import TranscriptStore
from .widget_factory import TranscriptWidgetFactory

if TYPE_CHECKING:
    from textual.widget import Widget

    from .app import NanoCodexApp


@dataclass(frozen=True)
class _UiEffects:
    """Batched side effects produced while reducing UI events."""

    transcript_changed: bool = False
    auto_scroll: bool = False
    flash: tuple[str, str, float] | None = None
    token_total: int | None = None
    reset_transcript: bool = False


@dataclass(frozen=True)
class _ScrollSnapshot:
    """Anchor used to preserve the current viewport across transcript re-renders."""

    anchor_node_id: str
    anchor_offset_y: float


UiOp = Callable[[], None]
_FLUSH_DEBOUNCE_SECONDS = 1 / 120
_AUTO_SCROLL_THRESHOLD = 2


class TuiEventReducer:
    """Reduce shared UI events into transcript state and app-level effects."""

    def __init__(self, store: TranscriptStore) -> None:
        self._store = store

    def apply(self, event: UiEvent) -> _UiEffects:
        """Apply one event and return the UI side effects required to render it."""
        match event:
            # Transcript mutations: append or complete visible conversation nodes.
            case UserMessageEvent(text=text) if text and text.strip():
                self._store.append_user_message(text)
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case AssistantTurnStarted(usage=usage):
                self._store.begin_turn(usage=usage)
                return _UiEffects(token_total=_extract_total_tokens(usage))
            case AssistantTextEvent(text=text) if text and text.strip():
                self._store.append_assistant_text(text)
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case AssistantReasoningEvent(text=text) if text and text.strip():
                self._store.append_assistant_reasoning(text)
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case ToolCallStarted(name=name, call_id=call_id, args_str=args_str):
                self._store.begin_tool_call(name=name, call_id=call_id, args_str=args_str)
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case ToolResultEvent(tool_name=tool_name, call_id=call_id, presentation=presentation):
                self._store.complete_tool_result(
                    tool_name=tool_name,
                    call_id=call_id,
                    presentation=presentation,
                )
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case SubagentScopeStarted():
                self._store.begin_subagent_scope()
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            case SubagentScopeEnded():
                self._store.end_subagent_scope()
                return _UiEffects()
            case CompactionSummaryEvent() as compact:
                self._store.append_compaction_summary(
                    total_tokens=compact.total_tokens,
                    max_tokens=compact.max_tokens,
                    strategy=compact.strategy,
                    remaining=compact.remaining,
                    current_tokens=compact.current_tokens,
                    summary_text=compact.summary_text,
                )
                return _UiEffects(transcript_changed=True, auto_scroll=True)
            # Session/control events mostly trigger flashes or transcript resets.
            case SessionStarted():
                self._store.clear()
                return _UiEffects(transcript_changed=True, reset_transcript=True, auto_scroll=True)
            case SessionRestored(path=path):
                return _UiEffects(flash=(f"Resumed session from {path}", "info", 3.0))
            case SessionSaved(path=path):
                return _UiEffects(flash=(f"Session saved to {path}", "success", 3.0))
            case InfoNotice(text=text):
                return _UiEffects(flash=(text, "info", 3.0))
            case WarningNotice(text=text):
                return _UiEffects(flash=(text, "warning", 5.0))
            case SessionEnded():
                return _UiEffects()
            case _:
                return _UiEffects()


class TuiTranscriptRenderer:
    """Render a windowed slice of the transcript store into Textual widgets."""

    def __init__(
        self,
        app: "NanoCodexApp",
        store: TranscriptStore,
        *,
        window_size: int = 120,
        page_size: int = 40,
    ) -> None:
        self._app = app
        self._store = store
        self._widget_factory = TranscriptWidgetFactory(store)
        self._window_size = window_size
        self._page_size = page_size
        self._window_start = 0
        self._window_end = 0
        self._last_top_level_count = 0
        self._mounted_ids: list[str] = []
        self._mounted_widgets: dict[str, Widget] = {}

    def reset(self) -> None:
        self._window_start, self._window_end = self._store.latest_window(self._window_size)
        self.render(auto_scroll=True, force_rebuild=True)

    def sync_to_tail(self) -> None:
        self._window_start, self._window_end = self._store.latest_window(self._window_size)

    def maybe_load_previous(self) -> bool:
        if self._window_start <= 0:
            return False

        scroll = self._app.query_one("#chat-window", VerticalScroll)
        if scroll.scroll_y > 1:
            return False

        visible_size = max(self._window_end - self._window_start, self._window_size)
        self._window_start, self._window_end = self._store.previous_window(
            start=self._window_start,
            size=visible_size,
            page_size=self._page_size,
        )
        self.render(auto_scroll=False, preserve_viewport=True)
        return True

    def include_new_tail_items(self) -> None:
        current_total = len(self._store.top_level_ids)
        if self._window_end == self._last_top_level_count and current_total > self._last_top_level_count:
            self._window_end = current_total

    def render(
        self,
        *,
        auto_scroll: bool,
        force_rebuild: bool = False,
        preserve_viewport: bool = False,
    ) -> None:
        from .app import Contents

        scroll = self._app.query_one("#chat-window", VerticalScroll)
        snapshot = self._capture_scroll_snapshot(scroll) if preserve_viewport and not auto_scroll else None
        contents = self._app.query_one("#contents", Contents)
        visible_ids = self._store.top_level_ids[self._window_start : self._window_end]
        if force_rebuild or not self._render_incrementally(contents, visible_ids):
            self._rebuild(contents, visible_ids)

        if auto_scroll:
            scroll.scroll_end(animate=False, immediate=True)
        elif snapshot is not None:
            self._restore_viewport_after_refresh(snapshot)

        self._last_top_level_count = len(self._store.top_level_ids)

    def _capture_scroll_snapshot(self, scroll: VerticalScroll) -> _ScrollSnapshot | None:
        """Capture the first visible transcript block as a stable scroll anchor."""
        viewport_top = scroll.scroll_offset.y

        for node_id in self._mounted_ids:
            widget = self._mounted_widgets.get(node_id)
            if widget is None or not widget.is_mounted:
                continue
            widget_top = widget.virtual_region.y
            widget_bottom = widget_top + widget.virtual_region.height
            if widget_bottom > viewport_top:
                return _ScrollSnapshot(
                    anchor_node_id=node_id,
                    anchor_offset_y=widget_top - viewport_top,
                )
        return None

    def _restore_viewport_after_refresh(
        self,
        snapshot: _ScrollSnapshot,
    ) -> None:
        """Restore the viewport after layout settles so re-renders do not visibly jump."""
        def restore() -> None:
            scroll = self._app.query_one("#chat-window", VerticalScroll)
            anchor_widget = self._mounted_widgets.get(snapshot.anchor_node_id)
            if anchor_widget is None or not anchor_widget.is_mounted:
                return
            target_y = anchor_widget.virtual_region.y - snapshot.anchor_offset_y
            target_y = max(0, min(target_y, scroll.max_scroll_y))
            scroll.scroll_to(y=target_y, animate=False, immediate=True, force=True)

        if not self._app.call_after_refresh(restore):
            restore()

    def _build_widget(self, node_id: str) -> "Widget | None":
        return self._widget_factory.build(node_id)

    def _render_incrementally(
        self,
        contents: VerticalScroll,
        visible_ids: list[str],
    ) -> bool:
        tail_update = _resolve_tail_update(self._mounted_ids, visible_ids)
        if tail_update is None:
            return False

        drop_count, append_ids = tail_update
        for node_id in self._mounted_ids[:drop_count]:
            widget = self._mounted_widgets.pop(node_id, None)
            if widget is not None:
                widget.remove()

        for node_id in self._mounted_ids[drop_count:]:
            widget = self._mounted_widgets.get(node_id)
            if widget is None:
                return False
            if not self._widget_factory.sync(widget, node_id):
                return False

        for node_id in append_ids:
            widget = self._build_widget(node_id)
            if widget is None:
                return False
            contents.mount(widget)
            self._mounted_widgets[node_id] = widget

        self._mounted_ids = list(visible_ids)
        return True

    def _rebuild(self, contents: VerticalScroll, visible_ids: list[str]) -> None:
        for child in list(contents.children):
            child.remove()

        self._mounted_ids = []
        self._mounted_widgets = {}
        for node_id in visible_ids:
            widget = self._build_widget(node_id)
            if widget is None:
                continue
            contents.mount(widget)
            self._mounted_ids.append(node_id)
            self._mounted_widgets[node_id] = widget

class TextualDisplay(UiEventSink, UiControlPort):
    """Textual UI runtime: event sink plus interactive control port."""

    def __init__(self, app: "NanoCodexApp", *, window_size: int = 120) -> None:
        self._app = app
        self._store = TranscriptStore()
        self._reducer = TuiEventReducer(self._store)
        self._renderer = TuiTranscriptRenderer(app, self._store, window_size=window_size)

        self._pending_events: deque[UiEvent] = deque()
        self._pending_ui_ops: deque[UiOp] = deque()
        self._flush_lock = threading.Lock()
        self._flush_scheduled = False
        self._follow_tail = True

    @property
    def store(self) -> TranscriptStore:
        return self._store

    def attach(self) -> None:
        self._renderer.reset()
        self._follow_tail = True

    def poll_window(self) -> None:
        self._refresh_follow_tail()
        if self._has_pending_transcript_work():
            return
        self._renderer.maybe_load_previous()

    def clear_transcript_view(self) -> None:
        def op() -> None:
            self._store.clear()
            self._renderer.reset()
            self._follow_tail = True

        self._queue_ui_op(op)

    def request_model_picker(
        self,
        models: tuple[str, ...],
        current: str | None,
    ) -> None:
        self._queue_ui_op(lambda: self._app._show_model_picker(list(models), current))

    def emit(self, event: UiEvent) -> None:
        with self._flush_lock:
            self._pending_events.append(event)
        self._schedule_flush()

    def _dispatch_ui_callback(self, callback: Callable[[], None]) -> bool:
        app_thread_id = getattr(self._app, "_thread_id", None)
        if app_thread_id == threading.get_ident():
            callback()
            return True

        try:
            self._app.call_from_thread(callback)
            return True
        except RuntimeError:
            return False

    def _queue_ui_op(self, op: UiOp) -> None:
        with self._flush_lock:
            self._pending_ui_ops.append(op)
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        should_schedule = False
        with self._flush_lock:
            if not self._flush_scheduled:
                self._flush_scheduled = True
                should_schedule = True

        if should_schedule:
            if not self._dispatch_ui_callback(self._enqueue_flush):
                with self._flush_lock:
                    self._flush_scheduled = False

    def _enqueue_flush(self) -> None:
        self._app.set_timer(_FLUSH_DEBOUNCE_SECONDS, self._flush_ui_ops)

    def _flush_ui_ops(self) -> None:
        with self._flush_lock:
            events = list(self._pending_events)
            self._pending_events.clear()
            ops = list(self._pending_ui_ops)
            self._pending_ui_ops.clear()
            self._flush_scheduled = False

        if not events and not ops:
            return

        with self._app.batch_update():
            if events:
                effects = _UiEffects()
                for event in events:
                    effects = _merge_effects(effects, self._reducer.apply(event))
                self._apply_effects(effects)
            for op in ops:
                op()

    def _apply_effects(self, effects: _UiEffects) -> None:
        self._refresh_follow_tail()
        auto_scroll = effects.auto_scroll and self._follow_tail
        if effects.token_total is not None:
            self._app.update_token_count(effects.token_total)
        if effects.flash is not None:
            text, style, duration = effects.flash
            self._app.flash_message(text, style=style, duration=duration)
        if effects.reset_transcript:
            self._renderer.sync_to_tail()
            auto_scroll = True
            self._follow_tail = True
        elif effects.transcript_changed:
            if auto_scroll:
                self._renderer.sync_to_tail()
            else:
                self._renderer.include_new_tail_items()

        if effects.transcript_changed or effects.reset_transcript:
            self._renderer.render(
                auto_scroll=auto_scroll,
                preserve_viewport=effects.transcript_changed and not auto_scroll,
            )
            if auto_scroll:
                self._follow_tail = True

    def _has_pending_transcript_work(self) -> bool:
        """Skip history preloading while transcript mutations are still queued for rendering."""
        with self._flush_lock:
            return bool(self._pending_events) or self._flush_scheduled

    def _refresh_follow_tail(self) -> None:
        self._follow_tail = self._is_near_tail()

    def _is_near_tail(self) -> bool:
        scroll = self._app.query_one("#chat-window", VerticalScroll)
        if scroll.max_scroll_y <= 0:
            return True
        if scroll.is_vertical_scroll_end:
            return True
        return (scroll.max_scroll_y - scroll.scroll_offset.y) <= _AUTO_SCROLL_THRESHOLD


def _resolve_tail_update(
    previous_ids: list[str],
    current_ids: list[str],
) -> tuple[int, list[str]] | None:
    """Detect whether the visible transcript changed by dropping head items and appending tail items."""
    for drop_count in range(len(previous_ids) + 1):
        kept_ids = previous_ids[drop_count:]
        if current_ids[: len(kept_ids)] == kept_ids:
            return drop_count, current_ids[len(kept_ids) :]
    return None


def _extract_total_tokens(usage: dict | None) -> int | None:
    """Extract the best available total token count from usage metadata."""
    if not usage:
        return None

    total = usage.get("total_token_count")
    if isinstance(total, int):
        return total

    input_tokens = usage.get("input_token_count") or 0
    output_tokens = usage.get("output_token_count") or 0
    if input_tokens or output_tokens:
        return int(input_tokens) + int(output_tokens)
    return None


def _merge_effects(base: _UiEffects, update: _UiEffects) -> _UiEffects:
    """Combine two UI effect batches into one render pass."""
    return _UiEffects(
        transcript_changed=base.transcript_changed or update.transcript_changed,
        auto_scroll=base.auto_scroll or update.auto_scroll,
        flash=update.flash or base.flash,
        token_total=update.token_total if update.token_total is not None else base.token_total,
        reset_transcript=base.reset_transcript or update.reset_transcript,
    )


__all__ = ["TextualDisplay", "TuiEventReducer", "TuiTranscriptRenderer"]
