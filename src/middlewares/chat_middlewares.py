"""Chat-stage middleware for response shaping and UI event emission."""

from __future__ import annotations

from copy import deepcopy
from typing import Awaitable, Callable, List

from agent_framework import ChatContext, ChatMiddleware, Content, Message, chat_middleware
from src.ui.presenters import AssistantResponsePresenter
from src.ui.protocol import NullUiEventSink, UiEventSink

from .middleware_registry import register_middleware


_MEDIA_MOVED_PLACEHOLDER = "Media content moved to a follow-up user message."


@register_middleware("strip_reasoning")
@chat_middleware
async def strip_reasoning_middleware(
    context: ChatContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """Remove reasoning items before sending messages to model backends."""

    messages = context.messages
    stripped_messages: List[Message] = []
    for message in messages:
        message.contents = [
            content
            for content in message.contents
            if content.type != "text_reasoning"
        ]
        stripped_messages.append(message)

    context.messages = stripped_messages
    await call_next()


@register_middleware("move_tool_media_to_user_message")
@chat_middleware
async def move_tool_media_to_user_message_middleware(
    context: ChatContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """Move rich tool-result items into follow-up user messages."""

    rewritten_messages: list[Message] = []
    for message in context.messages:
        if message.role != "tool":
            rewritten_messages.append(message)
            continue

        new_tool_message = deepcopy(message)
        new_tool_contents: list[Content] = []
        follow_up_messages: list[Message] = []
        for content in message.contents:
            if content.type != "function_result" or not content.items:
                new_tool_contents.append(content)
                continue

            if not any(item.type in {"data", "uri"} for item in content.items):
                new_tool_contents.append(content)
                continue

            tool_content = deepcopy(content)
            tool_content.items = [Content.from_text(_MEDIA_MOVED_PLACEHOLDER)]
            tool_content.result = _MEDIA_MOVED_PLACEHOLDER
            new_tool_contents.append(tool_content)

            follow_up_messages.append(
                Message(
                    "user",
                    [deepcopy(item) for item in content.items],
                    additional_properties={"generated_by": "move_tool_media_to_user_message"},
                )
            )

        new_tool_message.contents = new_tool_contents
        rewritten_messages.append(new_tool_message)
        rewritten_messages.extend(follow_up_messages)

    context.messages = rewritten_messages
    await call_next()


@register_middleware("logging_response")
class ChatUiMiddleware(ChatMiddleware):
    """Emit assistant transcript events through the shared UI sink."""

    def __init__(
        self,
        ui_sink: UiEventSink | None = None,
        presenter: AssistantResponsePresenter | None = None,
    ) -> None:
        self._ui_sink = ui_sink or NullUiEventSink()
        self._presenter = presenter or AssistantResponsePresenter()

    def clone(self, ui_sink: UiEventSink | None = None) -> "ChatUiMiddleware":
        """Create one middleware instance per agent/runtime binding."""
        return type(self)(ui_sink=ui_sink or self._ui_sink, presenter=self._presenter)

    async def process(
        self,
        context: ChatContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()

        if context.stream:
            return

        for event in self._presenter.events_for_response(
            messages=context.result.messages,
            usage=context.result.usage_details,
        ):
            self._ui_sink.emit(event)


__all__ = [
    "ChatUiMiddleware",
    "move_tool_media_to_user_message_middleware",
    "strip_reasoning_middleware",
]
