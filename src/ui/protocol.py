"""Typed UI runtime contracts shared by console and TUI renderers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .events import UiEvent


@runtime_checkable
class UiEventSink(Protocol):
    """Shared output surface used by middleware, workflows, and utilities."""

    def emit(self, event: UiEvent) -> None: ...


@runtime_checkable
class UiControlPort(Protocol):
    """Interactive-only controls that should not live in the shared event stream."""

    def clear_transcript_view(self) -> None: ...

    def request_model_picker(
        self,
        models: tuple[str, ...],
        current: str | None,
    ) -> None: ...


@dataclass(frozen=True)
class UiRuntime:
    """Bundle the shared sink plus any interactive-only controls."""

    sink: UiEventSink
    controls: UiControlPort | None = None


class NullUiEventSink:
    """No-op sink used when the caller does not provide a UI runtime."""

    def emit(self, event: UiEvent) -> None:
        del event


NULL_UI_RUNTIME = UiRuntime(sink=NullUiEventSink())


__all__ = [
    "NULL_UI_RUNTIME",
    "NullUiEventSink",
    "UiControlPort",
    "UiEventSink",
    "UiRuntime",
]
