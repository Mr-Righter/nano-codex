"""Nano-Codex function invocation layer with loop-managed compaction hooks."""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from functools import partial
from typing import Any, Literal, cast, overload

from agent_framework._sessions import AgentSession
from agent_framework._tools import (
    DEFAULT_MAX_CONSECUTIVE_ERRORS_PER_REQUEST,
    DEFAULT_MAX_ITERATIONS,
    FunctionInvocationLayer,
    _clear_internal_conversation_id,
    _execute_function_calls,
    _process_function_requests,
    _update_continuation_state,
)
from agent_framework._types import (
    ChatResponse,
    ChatResponseUpdate,
    Message,
    ResponseStream,
    add_usage_details,
)

from .history_compaction_runtime import LoopHistoryRuntime


def _response_history_delta(
    response: ChatResponse[Any],
    fcc_messages: Sequence[Message],
) -> list[Message]:
    """Return only newly generated messages for history storage.

    Upstream may return the already-persisted function call chain at the front
    of ``response.messages``. The loop runtime stores that chain separately, so
    only the real delta should be appended to full history.
    """
    if not fcc_messages:
        return list(response.messages)

    prefix = response.messages[: len(fcc_messages)]
    if len(prefix) == len(fcc_messages) and all(left is right for left, right in zip(prefix, fcc_messages)):
        return list(response.messages[len(fcc_messages) :])
    return list(response.messages)


def _normalize_required_tool_choice(options: dict[str, Any]) -> None:
    """Clear unsupported "required" choices once the loop is ready to stop."""
    if options.get("tool_choice") == "required" or (
        isinstance(options.get("tool_choice"), dict)
        and options.get("tool_choice", {}).get("mode") == "required"
    ):
        options["tool_choice"] = None


def _cache_response_usage(runtime: LoopHistoryRuntime, response: ChatResponse[Any]) -> None:
    """Persist the latest single-call total token usage for the next loop turn."""
    usage = response.usage_details
    total_token_count = usage.get("total_token_count") if isinstance(usage, Mapping) else None
    runtime.set_last_total_token_count(total_token_count if isinstance(total_token_count, int) else None)


async def _call_compacted_response(
    super_get_response: Callable[..., Awaitable[ChatResponse[Any]] | ResponseStream[ChatResponseUpdate, ChatResponse[Any]]],
    runtime: LoopHistoryRuntime,
    *,
    options: dict[str, Any],
    filtered_kwargs: dict[str, Any],
    compaction_strategy: Any,
    tokenizer: Any,
) -> ChatResponse[Any]:
    """Project full history to the compacted visible view before one model call."""
    prepared_messages = await runtime.prepare_messages(
        compaction_strategy=compaction_strategy,
        tokenizer=tokenizer,
    )
    return cast(
        ChatResponse[Any],
        await cast(
            Awaitable[ChatResponse[Any]],
            super_get_response(
                messages=prepared_messages,
                stream=False,
                options=options,
                compaction_strategy=None,
                tokenizer=None,
                client_kwargs=filtered_kwargs,
            ),
        ),
    )


async def _open_compacted_stream(
    super_get_response: Callable[..., Awaitable[ChatResponse[Any]] | ResponseStream[ChatResponseUpdate, ChatResponse[Any]]],
    runtime: LoopHistoryRuntime,
    *,
    options: dict[str, Any],
    filtered_kwargs: dict[str, Any],
    compaction_strategy: Any,
    tokenizer: Any,
) -> ResponseStream[ChatResponseUpdate, ChatResponse[Any]]:
    """Open a streaming response against the compacted visible history view."""
    prepared_messages = await runtime.prepare_messages(
        compaction_strategy=compaction_strategy,
        tokenizer=tokenizer,
    )
    stream_or_awaitable = super_get_response(
        messages=prepared_messages,
        stream=True,
        options=options,
        compaction_strategy=None,
        tokenizer=None,
        client_kwargs=filtered_kwargs,
    )
    if isinstance(stream_or_awaitable, ResponseStream):
        return cast(ResponseStream[ChatResponseUpdate, ChatResponse[Any]], stream_or_awaitable)
    return cast(
        ResponseStream[ChatResponseUpdate, ChatResponse[Any]],
        await cast(Awaitable[ResponseStream[ChatResponseUpdate, ChatResponse[Any]]], stream_or_awaitable),
    )


class NanoFunctionInvocationLayer(FunctionInvocationLayer[Any]):
    """Function invocation loop that compacts authoritative full history in place."""

    @overload
    def get_response(
        self,
        messages,
        *,
        stream: Literal[False] = ...,
        options: dict[str, Any] | None = None,
        middleware: Sequence[Any] | None = None,
        compaction_strategy: Any = None,
        tokenizer: Any = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> Awaitable[ChatResponse[Any]]: ...

    @overload
    def get_response(
        self,
        messages,
        *,
        stream: Literal[True],
        options: dict[str, Any] | None = None,
        middleware: Sequence[Any] | None = None,
        compaction_strategy: Any = None,
        tokenizer: Any = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse[Any]]: ...

    def get_response(
        self,
        messages,
        *,
        stream: bool = False,
        options: dict[str, Any] | None = None,
        middleware: Sequence[Any] | None = None,
        compaction_strategy: Any = None,
        tokenizer: Any = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> Awaitable[ChatResponse[Any]] | ResponseStream[ChatResponseUpdate, ChatResponse[Any]]:
        from agent_framework._middleware import ChatMiddlewareLayer, categorize_middleware

        # Bypass the upstream FunctionInvocationLayer so this custom loop owns
        # the raw model-call cadence, compaction timing, and per-call usage.
        super_get_response = ChatMiddlewareLayer.get_response.__get__(self, type(self))
        effective_client_kwargs = dict(client_kwargs) if client_kwargs is not None else {}
        if middleware is not None:
            existing = effective_client_kwargs.get("middleware", [])
            effective_client_kwargs["middleware"] = [
                *(
                    existing
                    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes))
                    else [existing]
                ),
                *middleware,
            ]
        runtime_middleware = categorize_middleware(effective_client_kwargs.pop("middleware", []))

        function_middleware_pipeline = self._get_function_middleware_pipeline(runtime_middleware["function"])
        if runtime_middleware["chat"]:
            effective_client_kwargs["middleware"] = runtime_middleware["chat"]
        max_errors = self.function_invocation_configuration.get(
            "max_consecutive_errors_per_request",
            DEFAULT_MAX_CONSECUTIVE_ERRORS_PER_REQUEST,
        )
        additional_function_arguments = (
            dict(function_invocation_kwargs) if function_invocation_kwargs is not None else {}
        )
        if options and (additional_opts := options.get("additional_function_arguments")):
            additional_function_arguments.update(cast(Mapping[str, Any], additional_opts))
        raw_session = effective_client_kwargs.get("session")
        invocation_session = raw_session if isinstance(raw_session, AgentSession) else None
        execute_function_calls = partial(
            _execute_function_calls,
            custom_args=additional_function_arguments,
            config=self.function_invocation_configuration,
            invocation_session=invocation_session,
            middleware_pipeline=function_middleware_pipeline,
        )
        filtered_kwargs = {k: v for k, v in effective_client_kwargs.items() if k != "session"}
        mutable_options: dict[str, Any] = dict(options) if options else {}
        mutable_options.pop("additional_function_arguments", None)
        response_format = mutable_options.get("response_format") if mutable_options else None
        resolved_compaction = self._resolve_compaction_overrides(
            compaction_strategy=compaction_strategy,
            tokenizer=tokenizer,
        )
        compaction_strategy = resolved_compaction.get("compaction_strategy")
        tokenizer = resolved_compaction.get("tokenizer")

        if not stream:

            async def _get_response() -> ChatResponse[Any]:
                errors_in_a_row = 0
                total_function_calls = 0
                max_function_calls = self.function_invocation_configuration.get("max_function_calls")
                runtime = LoopHistoryRuntime.from_inputs(messages, session=invocation_session)
                # Keep a full-history owner for the whole loop. Each model call
                # sees only the projected visible subset, but excluded messages
                # stay in session storage for future inspection and replay.
                prepped_messages = runtime.visible_history()
                response: ChatResponse[Any] | None = None
                aggregated_usage = None
                fcc_messages: list[Message] = []

                if not self.function_invocation_configuration.get("enabled", True):
                    response = await _call_compacted_response(
                        super_get_response,
                        runtime,
                        options=mutable_options,
                        filtered_kwargs=filtered_kwargs,
                        compaction_strategy=compaction_strategy,
                        tokenizer=tokenizer,
                    )
                    _cache_response_usage(runtime, response)
                    _update_continuation_state(
                        filtered_kwargs,
                        response,
                        session=invocation_session,
                        options=mutable_options,
                    )
                    runtime.append_messages(response.messages)
                    return _clear_internal_conversation_id(response)

                max_iterations = self.function_invocation_configuration.get("max_iterations", DEFAULT_MAX_ITERATIONS)
                for attempt_idx in range(max_iterations):
                    approval_result = await _process_function_requests(
                        response=None,
                        prepped_messages=prepped_messages,
                        tool_options=mutable_options,
                        attempt_idx=attempt_idx,
                        fcc_messages=None,
                        errors_in_a_row=errors_in_a_row,
                        max_errors=max_errors,
                        execute_function_calls=execute_function_calls,
                    )
                    if approval_result.get("action") == "stop":
                        response = ChatResponse(messages=prepped_messages)
                        break
                    errors_in_a_row = approval_result.get("errors_in_a_row", errors_in_a_row)
                    total_function_calls += approval_result.get("function_call_count", 0)

                    response = await _call_compacted_response(
                        super_get_response,
                        runtime,
                        options=mutable_options,
                        filtered_kwargs=filtered_kwargs,
                        compaction_strategy=compaction_strategy,
                        tokenizer=tokenizer,
                    )
                    _cache_response_usage(runtime, response)
                    aggregated_usage = add_usage_details(aggregated_usage, response.usage_details)
                    _update_continuation_state(
                        filtered_kwargs,
                        response,
                        session=invocation_session,
                        options=mutable_options,
                    )

                    result = await _process_function_requests(
                        response=response,
                        prepped_messages=None,
                        tool_options=mutable_options,
                        attempt_idx=attempt_idx,
                        fcc_messages=fcc_messages,
                        errors_in_a_row=errors_in_a_row,
                        max_errors=max_errors,
                        execute_function_calls=execute_function_calls,
                    )
                    if result.get("action") == "return":
                        runtime.append_messages(_response_history_delta(response, fcc_messages))
                        response.usage_details = aggregated_usage
                        return _clear_internal_conversation_id(response)

                    # Persist the full response into authoritative history
                    # before recomputing the next visible projection.
                    runtime.append_messages(response.messages)
                    total_function_calls += result.get("function_call_count", 0)
                    if result.get("action") == "stop" or (
                        max_function_calls is not None and total_function_calls >= max_function_calls
                    ):
                        mutable_options["tool_choice"] = "none"
                    errors_in_a_row = result.get("errors_in_a_row", errors_in_a_row)
                    _normalize_required_tool_choice(mutable_options)
                    prepped_messages = runtime.visible_history()

                if response is not None:
                    mutable_options["tool_choice"] = "none"

                response = await _call_compacted_response(
                    super_get_response,
                    runtime,
                    options=mutable_options,
                    filtered_kwargs=filtered_kwargs,
                    compaction_strategy=compaction_strategy,
                    tokenizer=tokenizer,
                )
                _cache_response_usage(runtime, response)
                aggregated_usage = add_usage_details(aggregated_usage, response.usage_details)
                _update_continuation_state(
                    filtered_kwargs,
                    response,
                    session=invocation_session,
                    options=mutable_options,
                )
                runtime.append_messages(response.messages)
                if fcc_messages:
                    # The caller still expects the function-call chain in the
                    # final response payload, even though it has already been
                    # accounted for in full history.
                    for msg in reversed(fcc_messages):
                        response.messages.insert(0, msg)
                response.usage_details = aggregated_usage
                return _clear_internal_conversation_id(response)

            return _get_response()

        async def _stream() -> AsyncIterable[ChatResponseUpdate]:
            errors_in_a_row = 0
            total_function_calls = 0
            max_function_calls = self.function_invocation_configuration.get("max_function_calls")
            runtime = LoopHistoryRuntime.from_inputs(messages, session=invocation_session)
            # Streaming follows the same full-history ownership model as the
            # non-streaming path; only the transport differs.
            prepped_messages = runtime.visible_history()
            response: ChatResponse[Any] | None = None
            fcc_messages: list[Message] = []

            if not self.function_invocation_configuration.get("enabled", True):
                inner_stream = await _open_compacted_stream(
                    super_get_response,
                    runtime,
                    options=mutable_options,
                    filtered_kwargs=filtered_kwargs,
                    compaction_strategy=compaction_strategy,
                    tokenizer=tokenizer,
                )
                await inner_stream
                async for update in inner_stream:
                    yield update
                final_response = await inner_stream.get_final_response()
                _cache_response_usage(runtime, final_response)
                _update_continuation_state(
                    filtered_kwargs,
                    final_response,
                    session=invocation_session,
                    options=mutable_options,
                )
                runtime.append_messages(final_response.messages)
                return

            max_iterations = self.function_invocation_configuration.get("max_iterations", DEFAULT_MAX_ITERATIONS)
            for attempt_idx in range(max_iterations):
                approval_result = await _process_function_requests(
                    response=None,
                    prepped_messages=prepped_messages,
                    tool_options=mutable_options,
                    attempt_idx=attempt_idx,
                    fcc_messages=None,
                    errors_in_a_row=errors_in_a_row,
                    max_errors=max_errors,
                    execute_function_calls=execute_function_calls,
                )
                errors_in_a_row = approval_result.get("errors_in_a_row", errors_in_a_row)
                total_function_calls += approval_result.get("function_call_count", 0)
                if approval_result.get("action") == "stop":
                    mutable_options["tool_choice"] = "none"
                    break

                inner_stream = await _open_compacted_stream(
                    super_get_response,
                    runtime,
                    options=mutable_options,
                    filtered_kwargs=filtered_kwargs,
                    compaction_strategy=compaction_strategy,
                    tokenizer=tokenizer,
                )
                await inner_stream
                async for update in inner_stream:
                    yield update

                response = await inner_stream.get_final_response()
                _cache_response_usage(runtime, response)
                _update_continuation_state(
                    filtered_kwargs,
                    response,
                    session=invocation_session,
                    options=mutable_options,
                )

                result = await _process_function_requests(
                    response=response,
                    prepped_messages=None,
                    tool_options=mutable_options,
                    attempt_idx=attempt_idx,
                    fcc_messages=fcc_messages,
                    errors_in_a_row=errors_in_a_row,
                    max_errors=max_errors,
                    execute_function_calls=execute_function_calls,
                )
                errors_in_a_row = result.get("errors_in_a_row", errors_in_a_row)
                total_function_calls += result.get("function_call_count", 0)
                if role := result.get("update_role"):
                    yield ChatResponseUpdate(
                        contents=result.get("function_call_results") or [],
                        role=role,
                    )

                if result.get("action") == "stop":
                    runtime.append_messages(response.messages)
                    mutable_options["tool_choice"] = "none"
                elif result.get("action") != "continue":
                    runtime.append_messages(_response_history_delta(response, fcc_messages))
                    return
                elif max_function_calls is not None and total_function_calls >= max_function_calls:
                    runtime.append_messages(response.messages)
                    mutable_options["tool_choice"] = "none"
                else:
                    runtime.append_messages(response.messages)

                _normalize_required_tool_choice(mutable_options)
                prepped_messages = runtime.visible_history()

            mutable_options["tool_choice"] = "none"
            final_inner_stream = await _open_compacted_stream(
                super_get_response,
                runtime,
                options=mutable_options,
                filtered_kwargs=filtered_kwargs,
                compaction_strategy=compaction_strategy,
                tokenizer=tokenizer,
            )
            await final_inner_stream
            async for update in final_inner_stream:
                yield update
            final_response = await final_inner_stream.get_final_response()
            _cache_response_usage(runtime, final_response)
            _update_continuation_state(
                filtered_kwargs,
                final_response,
                session=invocation_session,
                options=mutable_options,
            )
            runtime.append_messages(final_response.messages)

        def _finalize(updates: Sequence[ChatResponseUpdate]) -> ChatResponse[Any]:
            return ChatResponse.from_updates(updates, output_format_type=response_format)

        return ResponseStream(_stream(), finalizer=_finalize)


__all__ = ["NanoFunctionInvocationLayer"]
