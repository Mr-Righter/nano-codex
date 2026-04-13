"""Collapsible transcript container for delegated subagent output."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence

from textual import containers, events, on
from textual.app import ComposeResult
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import Static


class SubagentBlock(containers.VerticalGroup):
    """Collapsible container for subagent output. All messages emitted during
    a subagent execution are routed into this block instead of the main feed."""

    DEFAULT_CLASSES = "block"
    expanded: var[bool] = var(False, toggle_class="-expanded")

    def __init__(
        self,
        *,
        initial_children: Sequence[tuple[str, Widget]] | None = None,
        max_children: int = 120,
        expanded: bool = False,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        visible_children = list(initial_children or [])[-max_children:]
        self._max_children = max_children
        self._mounted_ids = [node_id for node_id, _ in visible_children]
        self._mounted_widgets = {node_id: widget for node_id, widget in visible_children}
        self._on_toggle = on_toggle
        self.expanded = expanded

    DEFAULT_CSS = """
    SubagentBlock {
        margin: 1 1 1 0;
        border-left: solid rgb(63, 128, 190);
        padding-left: 1;
        background: rgb(63, 128, 190) 6%;
    }
    SubagentBlock #subagent-header {
        pointer: pointer;
        color: rgb(63, 128, 190);
        padding: 0 0 0 0;
        text-style: bold;
    }
    SubagentBlock #subagent-header:hover {
        background: $panel;
    }
    SubagentBlock #subagent-inner {
        display: none;
        height: auto;
        layout: stream;
    }
    SubagentBlock.-expanded #subagent-inner {
        display: block;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("▶ Subagent", id="subagent-header")
        with containers.VerticalGroup(id="subagent-inner"):
            for child in self._mounted_widgets.values():
                yield child

    def sync_children(
        self,
        child_ids: Sequence[str],
        *,
        build_widget: Callable[[str], Widget | None],
        sync_widget: Callable[[Widget, str], bool],
    ) -> bool:
        visible_ids = list(child_ids[-self._max_children :])
        try:
            inner = self.query_one("#subagent-inner", containers.VerticalGroup)
        except Exception:
            return False

        tail_update = _resolve_tail_update(self._mounted_ids, visible_ids)
        if tail_update is None:
            return self._rebuild_children(inner, visible_ids, build_widget)

        drop_count, append_ids = tail_update
        for node_id in self._mounted_ids[:drop_count]:
            widget = self._mounted_widgets.pop(node_id, None)
            if widget is not None:
                widget.remove()

        for node_id in self._mounted_ids[drop_count:]:
            widget = self._mounted_widgets.get(node_id)
            if widget is None:
                return self._rebuild_children(inner, visible_ids, build_widget)
            if not sync_widget(widget, node_id):
                return self._rebuild_children(inner, visible_ids, build_widget)

        for node_id in append_ids:
            widget = build_widget(node_id)
            if widget is None:
                return self._rebuild_children(inner, visible_ids, build_widget)
            inner.mount(widget)
            self._mounted_widgets[node_id] = widget

        self._mounted_ids = visible_ids
        return True

    def _rebuild_children(
        self,
        inner: containers.VerticalGroup,
        child_ids: Sequence[str],
        build_widget: Callable[[str], Widget | None],
    ) -> bool:
        for child in list(inner.children):
            child.remove()

        self._mounted_ids = []
        self._mounted_widgets = {}
        for node_id in child_ids:
            widget = build_widget(node_id)
            if widget is None:
                return False
            inner.mount(widget)
            self._mounted_ids.append(node_id)
            self._mounted_widgets[node_id] = widget
        return True

    @on(events.Click, "#subagent-header")
    def toggle(self, event: events.Click) -> None:
        event.stop()
        self.expanded = not self.expanded
        if self._on_toggle is not None:
            self._on_toggle(self.expanded)

    def watch_expanded(self) -> None:
        symbol = "▼" if self.expanded else "▶"
        try:
            self.query_one("#subagent-header", Static).update(f"{symbol} Subagent")
        except Exception:
            pass


def _resolve_tail_update(
    previous_ids: list[str],
    current_ids: list[str],
) -> tuple[int, list[str]] | None:
    """Detect whether children changed by trimming the head and appending the tail."""
    for drop_count in range(len(previous_ids) + 1):
        kept_ids = previous_ids[drop_count:]
        if current_ids[: len(kept_ids)] == kept_ids:
            return drop_count, current_ids[len(kept_ids) :]
    return None
