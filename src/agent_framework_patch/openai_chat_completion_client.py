"""Nano-Codex compatibility layer over the Agent Framework Chat Completions client."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from itertools import chain
from typing import Any, Generic

from agent_framework.observability import ChatTelemetryLayer
from agent_framework._middleware import ChatMiddlewareLayer
from agent_framework_openai._chat_completion_client import (
    OpenAIChatCompletionOptionsT,
    RawOpenAIChatCompletionClient,
)
from openai import AsyncAzureOpenAI, AsyncOpenAI
from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from agent_framework._compaction import CompactionStrategy, TokenizerProtocol
from agent_framework._middleware import ChatAndFunctionMiddlewareTypes
from agent_framework._tools import FunctionInvocationConfiguration
from agent_framework._types import (
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FinishReason,
    Message,
)

from .function_invocation_layer import NanoFunctionInvocationLayer


def _extract_reasoning_payload(choice: Choice | ChunkChoice) -> tuple[str, Any] | None:
    """Extract provider-specific reasoning payloads from a choice."""
    message = choice.message if isinstance(choice, Choice) else choice.delta
    if message is None:
        return None

    if reasoning_details := getattr(message, "reasoning_details", None):
        return ("reasoning_details", reasoning_details)

    model_extra = getattr(message, "model_extra", None)
    if isinstance(model_extra, Mapping):
        for key in ("reasoning_content", "reasoning"):
            if value := model_extra.get(key):
                return (key, value)
    return None


def _decode_reasoning_content(protected_data: str) -> tuple[str, Any]:
    """Decode Nano-Codex reasoning payloads while preserving the original key."""
    try:
        payload = json.loads(protected_data)
    except json.JSONDecodeError:
        return ("reasoning_details", protected_data)

    if isinstance(payload, dict) and "key" in payload and "value" in payload:
        key = payload.get("key")
        if isinstance(key, str):
            return (key, payload.get("value"))

    return ("reasoning_details", payload)


class NanoOpenAIChatCompletionClient(  # type: ignore[misc]
    NanoFunctionInvocationLayer,
    ChatMiddlewareLayer[OpenAIChatCompletionOptionsT],
    ChatTelemetryLayer[OpenAIChatCompletionOptionsT],
    RawOpenAIChatCompletionClient[OpenAIChatCompletionOptionsT],
    Generic[OpenAIChatCompletionOptionsT],
):
    """OpenAI Chat Completions client with Nano-Codex compatibility behavior."""

    OTEL_PROVIDER_NAME = "openai"

    def __init__(
        self,
        model: str | None = None,
        *,
        model_id: str | None = None,
        api_key: str | Callable[[], str | Awaitable[str]] | None = None,
        credential: Any = None,
        org_id: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        async_client: AsyncOpenAI | AsyncAzureOpenAI | None = None,
        instruction_role: str | None = None,
        base_url: str | None = None,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        middleware: Sequence[ChatAndFunctionMiddlewareTypes] | None = None,
        function_invocation_configuration: FunctionInvocationConfiguration | None = None,
        compaction_strategy: CompactionStrategy | None = None,
        tokenizer: TokenizerProtocol | None = None,
    ) -> None:
        # ``model_id`` is kept as a compatibility alias for older Nano-Codex
        # configuration files; upstream Chat Completions uses ``model``.
        resolved_model = model or model_id
        super().__init__(
            model=resolved_model,
            api_key=api_key,
            credential=credential,
            org_id=org_id,
            default_headers=default_headers,
            async_client=async_client,
            instruction_role=instruction_role,
            base_url=base_url,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            middleware=middleware,
            function_invocation_configuration=function_invocation_configuration,
            compaction_strategy=compaction_strategy,
            tokenizer=tokenizer,
        )

    @property
    def model_id(self) -> str:
        return self.model

    @model_id.setter
    def model_id(self, value: str) -> None:
        self.model = value

    def _parse_response_from_openai(self, response: ChatCompletion, options: Mapping[str, Any]) -> ChatResponse:
        response_metadata = self._get_metadata_from_chat_response(response)
        messages: list[Message] = []
        finish_reason: FinishReason | None = None
        for choice in response.choices:
            response_metadata.update(self._get_metadata_from_chat_choice(choice))
            if choice.finish_reason:
                finish_reason = choice.finish_reason  # type: ignore[assignment]

            contents: list[Content] = []
            if text_content := self._parse_text_from_openai(choice):
                contents.append(text_content)
            if parsed_tool_calls := [tool for tool in self._parse_tool_calls_from_openai(choice)]:
                contents.extend(parsed_tool_calls)
            if reasoning_payload := _extract_reasoning_payload(choice):
                key, value = reasoning_payload
                contents.append(
                    Content.from_text_reasoning(
                        protected_data=json.dumps({"key": key, "value": value}, ensure_ascii=False)
                    )
                )
            messages.append(Message(role="assistant", contents=contents))

        return ChatResponse(
            response_id=response.id,
            created_at=datetime.fromtimestamp(response.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            usage_details=self._parse_usage_from_openai(response.usage) if response.usage else None,
            messages=messages,
            model=response.model,
            additional_properties=response_metadata,
            finish_reason=finish_reason,
            response_format=options.get("response_format"),
        )

    def _parse_response_update_from_openai(self, chunk: ChatCompletionChunk) -> ChatResponseUpdate:
        chunk_metadata = self._get_metadata_from_streaming_chat_response(chunk)
        contents: list[Content] = []
        finish_reason: FinishReason | None = None

        if chunk.usage:
            contents.append(
                Content.from_usage(usage_details=self._parse_usage_from_openai(chunk.usage), raw_representation=chunk)
            )

        for choice in chunk.choices:
            chunk_metadata.update(self._get_metadata_from_chat_choice(choice))
            contents.extend(self._parse_tool_calls_from_openai(choice))
            if choice.finish_reason:
                finish_reason = choice.finish_reason  # type: ignore[assignment]

            if text_content := self._parse_text_from_openai(choice):
                contents.append(text_content)
            if reasoning_payload := _extract_reasoning_payload(choice):
                key, value = reasoning_payload
                contents.append(
                    Content.from_text_reasoning(
                        protected_data=json.dumps({"key": key, "value": value}, ensure_ascii=False)
                    )
                )

        return ChatResponseUpdate(
            created_at=datetime.fromtimestamp(chunk.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            contents=contents,
            role="assistant",
            model=chunk.model,
            additional_properties=chunk_metadata,
            finish_reason=finish_reason,
            raw_representation=chunk,
            response_id=chunk.id,
            message_id=chunk.id,
        )

    def _prepare_messages_for_openai(
        self,
        chat_messages: Sequence[Message],
        role_key: str = "role",
        content_key: str = "content",
    ) -> list[dict[str, Any]]:
        # Some OpenAI-compatible endpoints reject multiple leading system
        # messages. Merge only the initial contiguous block and leave later
        # system messages untouched.
        list_of_lists = [self._prepare_message_for_openai(message) for message in chat_messages]
        flat_messages = list(chain.from_iterable(list_of_lists))

        leading_system_texts: list[str] = []
        remaining_messages: list[dict[str, Any]] = []
        for message in flat_messages:
            if not remaining_messages and message.get("role") == "system" and isinstance(message.get("content"), str):
                leading_system_texts.append(message["content"])
            else:
                remaining_messages.append(message)

        if not leading_system_texts:
            return flat_messages

        merged_system = {
            "role": "system",
            "content": "\n\n".join(leading_system_texts),
        }
        return [merged_system, *remaining_messages]

    def _prepare_message_for_openai(self, message: Message) -> list[dict[str, Any]]:
        if message.role in ("system", "developer"):
            texts = [content.text for content in message.contents if content.type == "text" and content.text]
            if texts:
                return [{"role": message.role, "content": "\n".join(texts)}]
            return []

        all_messages: list[dict[str, Any]] = []
        pending_reasoning: tuple[str, Any] | None = None

        for content in message.contents:
            if content.type in ("function_approval_request", "function_approval_response"):
                continue

            args: dict[str, Any] = {"role": message.role}

            for key in ("reasoning_details", "reasoning_content", "reasoning"):
                if details := message.additional_properties.get(key):
                    args[key] = details
                    break

            match content.type:
                case "function_call":
                    prepared = self._prepare_content_for_openai(content)
                    if all_messages and "tool_calls" in all_messages[-1]:
                        all_messages[-1]["tool_calls"].append(prepared)
                        continue
                    args["tool_calls"] = [prepared]
                case "function_result":
                    args["tool_call_id"] = content.call_id
                    if content.items:
                        text_parts = [item.text or "" for item in content.items if item.type == "text"]
                        args["content"] = "\n".join(text_parts) if text_parts else (
                            content.result if content.result is not None else ""
                        )
                    else:
                        args["content"] = content.result if content.result is not None else ""
                    all_messages.append(args)
                    continue
                case "text_reasoning" if content.protected_data is not None:
                    pending_reasoning = _decode_reasoning_content(content.protected_data)
                    continue
                case _:
                    args.setdefault("content", [])
                    args["content"].append(self._prepare_content_for_openai(content))

            if "content" in args or "tool_calls" in args:
                if pending_reasoning is not None:
                    key, value = pending_reasoning
                    args[key] = value
                    pending_reasoning = None
                all_messages.append(args)

        if pending_reasoning is not None:
            key, value = pending_reasoning
            if all_messages:
                all_messages[-1][key] = value
            else:
                all_messages.append({"role": message.role, "content": "", key: value})

        if message.role == "user" and all_messages:
            merged_content: list[Any] = []
            merged_message: dict[str, Any] | None = None
            for prepared_message in all_messages:
                if prepared_message.get("role") != "user" or "content" not in prepared_message:
                    continue
                if merged_message is None:
                    merged_message = {key: value for key, value in prepared_message.items() if key != "content"}
                for key in ("reasoning_details", "reasoning_content", "reasoning"):
                    if key in prepared_message and key not in merged_message:
                        merged_message[key] = prepared_message[key]
                prepared_content = prepared_message["content"]
                if isinstance(prepared_content, list):
                    merged_content.extend(prepared_content)
                else:
                    merged_content.append({"type": "text", "text": str(prepared_content)})
            if merged_message is not None:
                all_messages = [{**merged_message, "content": merged_content}]

        for prepared_message in all_messages:
            content = prepared_message.get("content")
            if isinstance(content, list):
                text_items: list[Mapping[str, Any]] = []
                for item in content:
                    if not isinstance(item, Mapping) or item.get("type") != "text":
                        break
                    text_items.append(item)
                else:
                    prepared_message["content"] = "\n".join(
                        item.get("text", "") if isinstance(item.get("text", ""), str) else ""
                        for item in text_items
                    )

        return all_messages


__all__ = ["NanoOpenAIChatCompletionClient"]
