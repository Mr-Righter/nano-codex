"""Helpers that translate framework objects into shared UI events."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_framework import Content, FunctionInvocationContext
from src.toolkit.tool_support import extract_display_text

from .events import (
    AssistantReasoningEvent,
    AssistantTextEvent,
    AssistantTurnStarted,
    BASH_TOOL_NAMES,
    EDIT_TOOL_NAMES,
    SUBAGENT_TOOL_NAME,
    SubagentScopeEnded,
    SubagentScopeStarted,
    ToolCallStarted,
    ToolPresentationModel,
    ToolResultEvent,
    UiEvent,
    extract_bash_display,
)


def _coerce_argument_payload(arguments: Any) -> Any:
    """Convert framework argument payloads into plain Python data when possible."""
    if hasattr(arguments, "model_dump"):
        return arguments.model_dump()
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except Exception:
            return None
    return arguments


def _coerce_argument_mapping(arguments: Any) -> dict[str, Any]:
    """Return a dictionary view of tool arguments or an empty mapping."""
    payload = _coerce_argument_payload(arguments)
    return payload if isinstance(payload, dict) else {}


def _extract_text_body(items: Sequence[Content]) -> str:
    """Join text content items into the display body used by presenters."""
    text_parts = [item.text for item in items if item.type == "text" and item.text]
    return "\n".join(text_parts)


def _read_text_file(path: str) -> str | None:
    """Read a text file for diff-style presentations, tolerating decoding issues."""
    path_obj = Path(path)
    if not path_obj.exists() or not path_obj.is_file():
        return None
    try:
        return path_obj.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _extract_result_media_type(items: Sequence[Content]) -> str | None:
    """Collect media type hints from a toolkit result payload."""
    media_type = None
    for item in items:
        if media_type is None:
            raw_media_type = getattr(item, "media_type", None)
            if isinstance(raw_media_type, str) and raw_media_type:
                media_type = raw_media_type
    return media_type


@dataclass(frozen=True)
class _ToolRunState:
    """Normalized tool invocation snapshot captured before execution finishes."""

    tool_name: str
    tool_call_id: str | None
    arguments: dict[str, Any]
    edit_path: str | None = None
    previous_text: str | None = None


@dataclass(frozen=True)
class ToolPresentationRequest:
    """Normalized input passed to one tool-result presentation builder."""

    tool_name: str
    tool_call_id: str | None
    arguments: dict[str, Any]
    result_items: Sequence[Content]
    display_text: str
    text_body: str
    media_type: str | None = None
    edit_path: str | None = None
    previous_text: str | None = None


ToolPresentationBuilder = Callable[[ToolPresentationRequest], ToolPresentationModel | None]


def _build_generic_text_presentation(request: ToolPresentationRequest) -> ToolPresentationModel:
    """Fallback text presentation for tools without a custom renderer."""
    body = request.text_body or request.display_text
    return ToolPresentationModel(
        kind="text",
        summary=request.display_text,
        body=body,
        media_type=request.media_type,
    )


def _build_summary_only_presentation(request: ToolPresentationRequest) -> ToolPresentationModel:
    """Presentation that keeps only the summary line."""
    return ToolPresentationModel(
        kind="text",
        summary=request.display_text,
        media_type=request.media_type,
    )


def _build_todo_presentation(request: ToolPresentationRequest) -> ToolPresentationModel:
    """Presentation optimized for the structured todo tool output."""
    body = request.text_body
    if body.startswith(request.display_text):
        body = body[len(request.display_text) :].lstrip()
    return ToolPresentationModel(
        kind="text",
        summary=request.display_text,
        body=body or None,
        media_type=request.media_type,
    )


def _build_bash_presentation(request: ToolPresentationRequest) -> ToolPresentationModel:
    """Presentation that extracts readable stdout/stderr from bash XML output."""
    body = extract_bash_display(request.display_text)
    return ToolPresentationModel(
        kind="text",
        summary=body,
        body=body,
    )


def _build_edit_presentation(request: ToolPresentationRequest) -> ToolPresentationModel | None:
    """Build a diff presentation by comparing the old and new file contents."""
    if request.edit_path is None:
        return None

    new_text = _read_text_file(request.edit_path)
    previous_text = request.previous_text or ""
    if new_text is None or new_text == previous_text:
        return None

    summary = request.display_text
    if not summary:
        summary = f"Edited file: {Path(request.edit_path).name}"
    return ToolPresentationModel(
        kind="diff",
        summary=summary,
        path=request.edit_path,
        old_text=previous_text,
        new_text=new_text,
        media_type=request.media_type,
    )


class ToolPresentationRegistry:
    """Registry that resolves the presentation builder for one tool result."""

    def __init__(
        self,
        *,
        tool_builders: Mapping[str, ToolPresentationBuilder] | None = None,
        fallback_builder: ToolPresentationBuilder | None = None,
    ) -> None:
        self._tool_builders = dict(tool_builders or self.default_tool_builders())
        self._fallback_builder = fallback_builder or _build_generic_text_presentation

    @staticmethod
    def default_tool_builders() -> dict[str, ToolPresentationBuilder]:
        builders: dict[str, ToolPresentationBuilder] = {
            "edit": _build_edit_presentation,
            "read": _build_summary_only_presentation,
            "use_skill": _build_summary_only_presentation,
            "view_image": _build_summary_only_presentation,
            "view_video": _build_summary_only_presentation,
            "write": _build_edit_presentation,
            "write_todos": _build_todo_presentation,
        }
        for name in BASH_TOOL_NAMES:
            builders[name] = _build_bash_presentation
        return builders

    def build(self, request: ToolPresentationRequest) -> ToolPresentationModel:
        builder = self._tool_builders.get(request.tool_name)
        if builder is not None and (presentation := builder(request)) is not None:
            return presentation

        return self._fallback_builder(request)


class AssistantResponsePresenter:
    """Translate chat responses into shared assistant transcript events."""

    def events_for_response(
        self,
        *,
        messages: Sequence[Any],
        usage: dict | None,
    ) -> list[UiEvent]:
        events: list[UiEvent] = []
        for message in messages:
            events.append(AssistantTurnStarted(usage=usage))
            for content in sorted(message.contents, key=lambda item: 0 if item.type == "text_reasoning" else 1):
                event = self._event_from_content(content)
                if event is not None:
                    events.append(event)
        return events

    def _event_from_content(self, content: Any) -> UiEvent | None:
        if content.type == "text_reasoning":
            payload = content.text or content.protected_data
            try:
                thinking = json.loads(payload)
            except Exception:
                thinking = payload
            if isinstance(thinking, dict) and "value" in thinking:
                thinking = thinking["value"]
            return AssistantReasoningEvent(text=str(thinking))

        if content.type == "function_call":
            if isinstance(content.arguments, dict):
                args_str = json.dumps(content.arguments, indent=2, ensure_ascii=False)
            else:
                try:
                    args_str = json.dumps(json.loads(content.arguments), indent=2, ensure_ascii=False)
                except Exception:
                    args_str = str(content.arguments)
            return ToolCallStarted(name=content.name, call_id=content.call_id, args_str=args_str)

        if content.type == "text" and content.text:
            return AssistantTextEvent(text=content.text)

        return None


class ToolResultPresenter:
    """Translate tool invocation lifecycle into shared tool UI events."""

    def __init__(self, registry: ToolPresentationRegistry | None = None) -> None:
        self._registry = registry or ToolPresentationRegistry()

    def capture_state(self, context: FunctionInvocationContext) -> _ToolRunState:
        arguments = _coerce_argument_mapping(context.arguments)
        tool_name = context.function.name
        edit_path = None
        previous_text = None
        if tool_name in EDIT_TOOL_NAMES:
            candidate = arguments.get("file_path")
            if isinstance(candidate, str) and candidate:
                edit_path = candidate
                previous_text = _read_text_file(candidate)
        return _ToolRunState(
            tool_name=tool_name,
            tool_call_id=context.metadata.get("tool_call_id"),
            arguments=arguments,
            edit_path=edit_path,
            previous_text=previous_text,
        )

    def start_events(self, state: _ToolRunState) -> list[UiEvent]:
        if state.tool_name != SUBAGENT_TOOL_NAME:
            return []
        return [SubagentScopeStarted()]

    def finish_events(
        self,
        state: _ToolRunState,
        result_items: Sequence[Content],
    ) -> list[UiEvent]:
        media_type = _extract_result_media_type(result_items)
        request = ToolPresentationRequest(
            tool_name=state.tool_name,
            tool_call_id=state.tool_call_id,
            arguments=state.arguments,
            result_items=result_items,
            display_text=extract_display_text(result_items),
            text_body=_extract_text_body(result_items),
            media_type=media_type,
            edit_path=state.edit_path,
            previous_text=state.previous_text,
        )
        presentation = self._registry.build(request)
        events: list[UiEvent] = [
            ToolResultEvent(
                tool_name=state.tool_name,
                call_id=state.tool_call_id,
                presentation=presentation,
            )
        ]

        if state.tool_name == SUBAGENT_TOOL_NAME:
            events.append(SubagentScopeEnded())
        return events

    def failure_events(self, state: _ToolRunState) -> list[UiEvent]:
        if state.tool_name != SUBAGENT_TOOL_NAME:
            return []
        return [SubagentScopeEnded()]


__all__ = [
    "AssistantResponsePresenter",
    "ToolPresentationBuilder",
    "ToolPresentationRegistry",
    "ToolPresentationRequest",
    "ToolResultPresenter",
]
