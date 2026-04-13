"""DiffBlock — collapsible diff widget for file edits in the Nano-Codex TUI.

Ported and adapted from toad's DiffView widget (toad/src/toad/widgets/diff_view.py).
Key differences from the original toad DiffView:
  - Wrapped in a collapsible DiffBlock container that keeps the public API
    DiffBlock(path, old_text, new_text) unchanged.
  - DiffView is lazily mounted inside DiffBlock only when first expanded.
  - loop_last() inlined (no toad._loop dependency).
  - auto_split removed; default is unified view with a [unified | split] toggle.
"""

from __future__ import annotations

import asyncio
import difflib
from itertools import starmap
from typing import Iterable, Literal

from rich.segment import Segment
from rich.style import Style as RichStyle

from textual import containers, events, highlight, on
from textual.app import ComposeResult
from textual.content import Content, Span
from textual.geometry import Size
from textual.reactive import reactive, var
from textual.selection import Selection
from textual.strip import Strip
from textual.style import Style
from textual.visual import Visual, RenderOptions
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

type Annotation = Literal["+", "-", "/", " "]


def _loop_last(iterable: Iterable) -> Iterable[tuple[bool, object]]:
    """Yield (is_last, item) for each item in *iterable*."""
    it = iter(iterable)
    try:
        prev = next(it)
    except StopIteration:
        return
    for item in it:
        yield False, prev
        prev = item
    yield True, prev


def _fill_lists(list_a: list, list_b: list, fill_value: object) -> None:
    """Extend the shorter list with *fill_value* until both have equal length."""
    diff = len(list_a) - len(list_b)
    if diff > 0:
        list_b.extend([fill_value] * diff)
    elif diff < 0:
        list_a.extend([fill_value] * (-diff))


# ---------------------------------------------------------------------------
# Low-level rendering primitives (ported from toad DiffView)
# ---------------------------------------------------------------------------

class LineContent(Visual):
    """Renders a list of syntax-highlighted Content lines into Textual Strips."""

    def __init__(
        self,
        code_lines: list[Content | None],
        line_styles: list[str],
        width: int | None = None,
    ) -> None:
        self.code_lines = code_lines
        self.line_styles = line_styles
        self._width = width

    def render_strips(
        self, width: int, height: int | None, style: Style, options: RenderOptions
    ) -> list[Strip]:
        strips: list[Strip] = []
        selection = options.selection
        selection_style = options.selection_style or Style.null()
        for y, (line, color) in enumerate(zip(self.code_lines, self.line_styles)):
            if line is None:
                line = Content.styled("╲" * width, "$foreground 15%")
            else:
                if selection is not None:
                    if span := selection.get_span(y):
                        start, end = span
                        if end == -1:
                            end = len(line)
                        line = line.stylize(selection_style, start, end)
                if line.cell_length < width:
                    line = line.pad_right(width - line.cell_length)
            line = line.stylize_before(color).stylize_before(style)
            x = 0
            meta: dict = {"offset": (x, y)}
            segments = []
            for text, rich_style, _ in line.render_segments():
                if rich_style is not None:
                    meta["offset"] = (x, y)
                    segments.append(
                        Segment(text, rich_style + RichStyle.from_meta(meta))
                    )
                else:
                    segments.append(Segment(text, rich_style))
                x += len(text)
            strips.append(Strip(segments, line.cell_length))
        return strips

    def get_optimal_width(self, rules, container_width: int) -> int:
        if self._width is not None:
            return self._width
        return max(
            (line.cell_length for line in self.code_lines if line is not None),
            default=0,
        )

    def get_minimal_width(self, rules) -> int:
        return 1

    def get_height(self, rules, width: int) -> int:
        return len(self.line_styles)


class LineAnnotations(Widget):
    """Vertical strip showing line numbers and diff markers (+/-/ )."""

    DEFAULT_CSS = """
    LineAnnotations {
        width: auto;
        height: auto;
    }
    """

    numbers: reactive[list[Content]] = reactive(list)

    def __init__(
        self,
        numbers: Iterable[Content],
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self.numbers = list(numbers)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return max((n.cell_length for n in self.numbers), default=0)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self.numbers)

    def render_line(self, y: int) -> Strip:
        width = self.get_content_width(self.size, self.app.size)
        visual_style = self.visual_style
        rich_style = visual_style.rich_style
        try:
            number = self.numbers[y]
        except IndexError:
            number = Content.empty()
        strip = Strip(
            number.render_segments(visual_style), cell_length=number.cell_length
        )
        return strip.adjust_cell_length(width, rich_style)


class DiffCode(Static):
    """Container that displays a LineContent Visual object."""

    DEFAULT_CSS = """
    DiffCode {
        width: auto;
        height: auto;
        min-width: 1fr;
    }
    """
    ALLOW_SELECT = True

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        visual = self._render()
        if isinstance(visual, LineContent):
            text = "\n".join(
                "" if line is None else line.plain for line in visual.code_lines
            )
            return selection.extract(text), "\n"
        return None


class DiffScrollContainer(containers.HorizontalGroup):
    """Horizontally scrollable pane that can optionally be linked to a sibling."""

    scroll_link: var[Widget | None] = var(None)

    DEFAULT_CSS = """
    DiffScrollContainer {
        overflow: scroll hidden;
        scrollbar-size: 0 0;
        height: auto;
    }
    """

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_x(old_value, new_value)
        if self.scroll_link:
            self.scroll_link.scroll_x = new_value


# ---------------------------------------------------------------------------
# Style tables
# ---------------------------------------------------------------------------

_NUMBER_STYLES: dict[str, str] = {
    "+": "$text-success 80% on $success 20%",
    "-": "$text-error 80% on $error 20%",
    " ": "$foreground 30% on $foreground 3%",
}
_LINE_STYLES: dict[str, str] = {
    "+": "on $success 10%",
    "-": "on $error 10%",
    " ": "",
    "/": "",
}
_EDGE_STYLES: dict[str, str] = {
    "+": "$text-success 30% on $success 20%",
    "-": "$text-error 30% on $error 20%",
    " ": "$foreground 10% on $foreground 3%",
}


# ---------------------------------------------------------------------------
# DiffView — unified / split diff renderer (adapted from toad)
# ---------------------------------------------------------------------------

class DiffView(containers.VerticalGroup):
    """Syntax-highlighted unified or split diff view.

    Adapted from toad's DiffView; auto_split removed for simplicity.
    Toggle ``split`` reactive to switch between unified and split layouts.
    """

    split: reactive[bool] = reactive(False, recompose=True)

    DEFAULT_CSS = """
    DiffView {
        width: 1fr;
        height: auto;
    }
    DiffView .diff-group {
        height: auto;
        background: $foreground 4%;
    }
    DiffView .ellipsis {
        text-align: center;
        width: 1fr;
        color: $text-primary;
        text-style: bold;
    }
    """

    def __init__(
        self,
        path: str,
        code_before: str,
        code_after: str,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._path = path
        self._code_before = code_before.expandtabs()
        self._code_after = code_after.expandtabs()
        self._grouped_opcodes: list[list[tuple[str, int, int, int, int]]] | None = None
        self._highlighted_code_lines: tuple[list[Content], list[Content]] | None = None

    # ------------------------------------------------------------------
    # Lazy computed properties
    # ------------------------------------------------------------------

    @property
    def grouped_opcodes(self) -> list[list[tuple[str, int, int, int, int]]]:
        if self._grouped_opcodes is None:
            sm = difflib.SequenceMatcher(
                lambda c: c in {" ", "\t"},
                self._code_before.splitlines(),
                self._code_after.splitlines(),
                autojunk=True,
            )
            self._grouped_opcodes = list(sm.get_grouped_opcodes())
        return self._grouped_opcodes

    @staticmethod
    def _highlight_diff_lines(
        lines_a: list[Content], lines_b: list[Content]
    ) -> tuple[list[Content], list[Content]]:
        """Add character-level diff highlights to same-count replace blocks."""
        code_a = Content("\n").join(lines_a)
        code_b = Content("\n").join(lines_b)
        sm = difflib.SequenceMatcher(
            lambda c: c in {" ", "\t"}, code_a.plain, code_b.plain, autojunk=True
        )
        spans_a: list[Span] = []
        spans_b: list[Span] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in {"delete", "replace"}:
                spans_a.append(Span(i1, i2, "on $error 30%"))
            if tag in {"insert", "replace"}:
                spans_b.append(Span(j1, j2, "on $success 30%"))
        return (
            code_a.add_spans(spans_a).split("\n"),
            code_b.add_spans(spans_b).split("\n"),
        )

    @property
    def highlighted_code_lines(self) -> tuple[list[Content], list[Content]]:
        if self._highlighted_code_lines is None:
            lang_a = highlight.guess_language(self._code_before, self._path)
            lang_b = highlight.guess_language(self._code_after, self._path)
            lines_a = highlight.highlight(
                "\n".join(self._code_before.splitlines()),
                language=lang_a,
                path=self._path,
            ).split("\n")
            lines_b = highlight.highlight(
                "\n".join(self._code_after.splitlines()),
                language=lang_b,
                path=self._path,
            ).split("\n")
            for group in self.grouped_opcodes:
                for tag, i1, i2, j1, j2 in group:
                    if tag == "replace" and (j2 - j1) == (i2 - i1):
                        diff_a, diff_b = self._highlight_diff_lines(
                            lines_a[i1:i2], lines_b[j1:j2]
                        )
                        lines_a[i1:i2] = diff_a
                        lines_b[j1:j2] = diff_b
            self._highlighted_code_lines = (lines_a, lines_b)
        return self._highlighted_code_lines

    async def prepare(self) -> None:
        """Run CPU-heavy diff/highlight work in a background thread."""
        await asyncio.to_thread(
            lambda: (self.grouped_opcodes, self.highlighted_code_lines)
        )

    @property
    def counts(self) -> tuple[int, int]:
        additions = removals = 0
        for group in self.grouped_opcodes:
            for tag, i1, i2, j1, j2 in group:
                if tag == "delete":
                    removals += i2 - i1
                elif tag == "replace":
                    additions += j2 - j1
                    removals += i2 - i1
                elif tag == "insert":
                    additions += j2 - j1
        return additions, removals

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        if self.split:
            yield from self._compose_split()
        else:
            yield from self._compose_unified()

    def _compose_unified(self) -> ComposeResult:
        lines_a, lines_b = self.highlighted_code_lines
        for last, group in _loop_last(self.grouped_opcodes):
            line_numbers_a: list[int | None] = []
            line_numbers_b: list[int | None] = []
            annotations: list[str] = []
            code_lines: list[Content | None] = []
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for off, line in enumerate(lines_a[i1:i2], 1):
                        annotations.append(" ")
                        line_numbers_a.append(i1 + off)
                        line_numbers_b.append(j1 + off)
                        code_lines.append(line)
                    continue
                if tag in {"delete", "replace"}:
                    for off, line in enumerate(lines_a[i1:i2], 1):
                        annotations.append("-")
                        line_numbers_a.append(i1 + off)
                        line_numbers_b.append(None)
                        code_lines.append(line)
                if tag in {"insert", "replace"}:
                    for off, line in enumerate(lines_b[j1:j2], 1):
                        annotations.append("+")
                        line_numbers_a.append(None)
                        line_numbers_b.append(j1 + off)
                        code_lines.append(line)

            ln_width = max(
                (len(str(n)) for n in line_numbers_a + line_numbers_b if n is not None),
                default=1,
            )
            with containers.HorizontalGroup(classes="diff-group"):
                yield LineAnnotations(
                    [
                        (Content(f"▎{' ' * ln_width} ") if n is None
                         else Content(f"▎{n:>{ln_width}} "))
                        .stylize(_NUMBER_STYLES[a], 1)
                        .stylize(_EDGE_STYLES[a], 0, 1)
                        for n, a in zip(line_numbers_a, annotations)
                    ]
                )
                yield LineAnnotations(
                    [
                        (Content(f" {' ' * ln_width} ") if n is None
                         else Content(f" {n:>{ln_width}} "))
                        .stylize(_NUMBER_STYLES[a])
                        for n, a in zip(line_numbers_b, annotations)
                    ]
                )
                yield LineAnnotations(
                    [
                        Content(f" {a} ").stylize(_LINE_STYLES[a]).stylize("bold")
                        for a in annotations
                    ]
                )
                with DiffScrollContainer():
                    yield DiffCode(
                        LineContent(code_lines, [_LINE_STYLES[a] for a in annotations])
                    )
            if not last:
                yield Static("⋮", classes="ellipsis")

    def _compose_split(self) -> ComposeResult:
        lines_a, lines_b = self.highlighted_code_lines
        annotation_hatch = Content.styled("╲" * 3, "$foreground 15%")
        annotation_blank = Content(" " * 3)

        def make_annotation(ann: Annotation, highlight_ann: str) -> Content:
            if ann == highlight_ann:
                return Content(f" {ann} ").stylize(_LINE_STYLES[ann]).stylize("bold")
            if ann == "/":
                return annotation_hatch
            return annotation_blank

        for last, group in _loop_last(self.grouped_opcodes):
            lna: list[int | None] = []
            lnb: list[int | None] = []
            ann_a: list[Annotation] = []
            ann_b: list[Annotation] = []
            cls_a: list[Content | None] = []
            cls_b: list[Content | None] = []
            for tag, i1, i2, j1, j2 in group:
                if tag == "equal":
                    for off, line in enumerate(lines_a[i1:i2], 1):
                        ann_a.append(" ")
                        ann_b.append(" ")
                        lna.append(i1 + off)
                        lnb.append(j1 + off)
                        cls_a.append(line)
                        cls_b.append(line)
                else:
                    if tag in {"delete", "replace"}:
                        for ln, line in enumerate(lines_a[i1:i2], i1 + 1):
                            ann_a.append("-")
                            lna.append(ln)
                            cls_a.append(line)
                    if tag in {"insert", "replace"}:
                        for ln, line in enumerate(lines_b[j1:j2], j1 + 1):
                            ann_b.append("+")
                            lnb.append(ln)
                            cls_b.append(line)
                    _fill_lists(cls_a, cls_b, None)
                    _fill_lists(ann_a, ann_b, "/")
                    _fill_lists(lna, lnb, None)

            ln_width = max(
                (len(str(n)) for n in lna + lnb if n is not None), default=1
            )
            hatch = Content.styled("╲" * (2 + ln_width), "$foreground 15%")

            def format_number(n: int | None, a: str) -> Content:
                return hatch if n is None else (
                    Content(f"▎{n:>{ln_width}} ")
                    .stylize(_NUMBER_STYLES[a], 1)
                    .stylize(_EDGE_STYLES[a], 0, 1)
                )

            line_width = max(
                (line.cell_length for line in cls_a + cls_b if line is not None),
                default=0,
            )
            with containers.HorizontalGroup(classes="diff-group"):
                yield LineAnnotations(list(starmap(format_number, zip(lna, ann_a))))
                yield LineAnnotations([make_annotation(a, "-") for a in ann_a])
                with DiffScrollContainer() as sc_a:
                    yield DiffCode(
                        LineContent(cls_a, [_LINE_STYLES[a] for a in ann_a], width=line_width)
                    )
                yield LineAnnotations(list(starmap(format_number, zip(lnb, ann_b))))
                yield LineAnnotations([make_annotation(a, "+") for a in ann_b])
                with DiffScrollContainer() as sc_b:
                    yield DiffCode(
                        LineContent(cls_b, [_LINE_STYLES[a] for a in ann_b], width=line_width)
                    )
                sc_a.scroll_link = sc_b
                sc_b.scroll_link = sc_a

            if not last:
                with containers.HorizontalGroup():
                    yield Static("⋮", classes="ellipsis")
                    yield Static("⋮", classes="ellipsis")


# ---------------------------------------------------------------------------
# DiffBlock — public API wrapper (collapsible, lazy-mounts DiffView)
# ---------------------------------------------------------------------------

class DiffBlock(containers.VerticalGroup):
    """Collapsible diff widget. Public API: DiffBlock(path, old_text, new_text).

    Wraps DiffView with a clickable header. DiffView is mounted lazily on first
    expand to avoid blocking the event loop on large diffs.
    """

    DEFAULT_CSS = """
    DiffBlock {
        margin: 0 1 1 1;
        height: auto;
        border: solid $panel;
        border-left: solid $success;
    }
    DiffBlock #diff-header {
        background: $panel;
        color: $text-secondary;
        padding: 0 1;
        pointer: pointer;
    }
    DiffBlock #diff-header:hover {
        background: $primary 10%;
    }
    DiffBlock #diff-body {
        display: none;
    }
    DiffBlock.-expanded #diff-body {
        display: block;
    }
    DiffBlock #split-toggle {
        color: $text-muted;
        text-style: dim;
        padding: 0 1;
        pointer: pointer;
        text-align: right;
        height: 1;
        width: 1fr;
    }
    DiffBlock #split-toggle:hover {
        color: $primary;
        text-style: bold;
    }
    """

    expanded: var[bool] = var(False, toggle_class="-expanded")

    def __init__(self, path: str, old_text: str, new_text: str) -> None:
        super().__init__()
        self._path = path
        self._old_text = old_text
        self._new_text = new_text
        self._diff_view: DiffView | None = None
        self._counts: tuple[int, int] | None = None

    def _get_counts(self) -> tuple[int, int]:
        """Compute added/removed line counts via fast unified_diff (cached)."""
        if self._counts is None:
            diff = list(
                difflib.unified_diff(
                    self._old_text.splitlines(keepends=True),
                    self._new_text.splitlines(keepends=True),
                    lineterm="",
                )
            )
            added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
            self._counts = (added, removed)
        return self._counts

    def _header_markup(self) -> str:
        added, removed = self._get_counts()
        symbol = "▼" if self.expanded else "▶"
        return f"📄 {self._path}  [+{added} -{removed}]  {symbol}"

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), id="diff-header")
        with containers.VerticalGroup(id="diff-body"):
            yield Static("[unified | split]", id="split-toggle")

    def watch_expanded(self) -> None:
        try:
            self.query_one("#diff-header", Static).update(self._header_markup())
        except Exception:
            pass
        if self.expanded and self._diff_view is None:
            # Lazy-mount DiffView on first expand
            self._diff_view = DiffView(self._path, self._old_text, self._new_text)
            try:
                body = self.query_one("#diff-body")
                body.mount(self._diff_view, before=self.query_one("#split-toggle"))
                # Run CPU-heavy work in a thread, refresh when done
                self.run_worker(self._prepare_diff, thread=True)
            except Exception:
                pass

    async def _prepare_diff(self) -> None:
        """Worker: compute diff/highlight in background, then recompose DiffView."""
        if self._diff_view is not None:
            await self._diff_view.prepare()
            self._diff_view.refresh(recompose=True)

    @on(events.Click, "#diff-header")
    def toggle_expand(self, event: events.Click) -> None:
        event.stop()
        self.expanded = not self.expanded

    @on(events.Click, "#split-toggle")
    def toggle_split(self, event: events.Click) -> None:
        event.stop()
        if self._diff_view is not None:
            self._diff_view.split = not self._diff_view.split
