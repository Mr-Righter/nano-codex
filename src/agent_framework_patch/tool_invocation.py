"""Minimal Agent Framework patch for tool-call metadata propagation."""

from __future__ import annotations

from typing import Any


async def _patched_auto_invoke_function(
    function_call_content: Any,
    custom_args: dict[str, Any] | None = None,
    *,
    config: dict[str, Any],
    tool_map: dict[str, Any],
    invocation_session: Any = None,
    sequence_index: int | None = None,
    request_index: int | None = None,
    middleware_pipeline: Any = None,
) -> Any:
    """Mirror upstream tool invocation while attaching ``tool_call_id`` metadata.

    The patch intentionally avoids broader result-shaping changes. Its only
    Nano-Codex-specific responsibility is making the original OpenAI tool call
    id visible to function middleware and the TUI correlation layer.
    """
    from pydantic import ValidationError

    from agent_framework._middleware import FunctionInvocationContext, MiddlewareTermination
    from agent_framework._tools import _validate_arguments_against_schema
    from agent_framework._types import Content
    from agent_framework.exceptions import UserInputRequiredException

    tool = None
    if function_call_content.type == "function_call":
        tool = tool_map.get(function_call_content.name)
        if tool is None:
            exc = KeyError(f'Function "{function_call_content.name}" not found.')
            return Content.from_function_result(
                call_id=function_call_content.call_id,
                result=f'Error: Requested function "{function_call_content.name}" not found.',
                exception=str(exc),
                additional_properties=function_call_content.additional_properties,
            )
    else:
        inner_call = function_call_content.function_call
        if inner_call.type != "function_call":
            return function_call_content
        tool = tool_map.get(inner_call.name)
        if tool is None:
            return function_call_content
        function_call_content = inner_call

    parsed_args = dict(function_call_content.parse_arguments() or {})
    runtime_kwargs = {
        key: value
        for key, value in (custom_args or {}).items()
        if key not in {"_function_middleware_pipeline", "middleware", "conversation_id"}
    }
    if invocation_session is not None:
        runtime_kwargs["session"] = invocation_session

    try:
        if not getattr(tool, "_schema_supplied", False) and tool.input_model is not None:
            args = tool.input_model.model_validate(parsed_args).model_dump(exclude_none=True)
        else:
            args = dict(parsed_args)
        args = _validate_arguments_against_schema(
            arguments=args,
            schema=tool.parameters(),
            tool_name=tool.name,
        )
    except (TypeError, ValidationError) as exc:
        message = "Error: Argument parsing failed."
        if config.get("include_detailed_errors", False):
            message = f"{message} Exception: {exc}"
        return Content.from_function_result(
            call_id=function_call_content.call_id,
            result=message,
            exception=str(exc),
            additional_properties=function_call_content.additional_properties,
        )

    metadata = {"tool_call_id": function_call_content.call_id}

    if middleware_pipeline is None or not middleware_pipeline.has_middlewares:
        try:
            direct_context = None
            if getattr(tool, "_context_parameter_name", None):
                direct_context = FunctionInvocationContext(
                    function=tool,
                    arguments=args,
                    session=invocation_session,
                    kwargs=runtime_kwargs.copy(),
                    metadata=metadata.copy(),
                )
            function_result = await tool.invoke(
                arguments=args,
                context=direct_context,
                tool_call_id=function_call_content.call_id,
            )
            return Content.from_function_result(
                call_id=function_call_content.call_id,
                result=function_result,
                additional_properties=function_call_content.additional_properties,
            )
        except UserInputRequiredException:
            raise
        except Exception as exc:
            message = "Error: Function failed."
            if config.get("include_detailed_errors", False):
                message = f"{message} Exception: {exc}"
            return Content.from_function_result(
                call_id=function_call_content.call_id,
                result=message,
                exception=str(exc),
                additional_properties=function_call_content.additional_properties,
            )

    middleware_context = FunctionInvocationContext(
        function=tool,
        arguments=args,
        session=invocation_session,
        kwargs=runtime_kwargs.copy(),
        metadata=metadata.copy(),
    )

    async def final_function_handler(context_obj: Any) -> Any:
        return await tool.invoke(
            arguments=context_obj.arguments,
            context=context_obj,
            tool_call_id=function_call_content.call_id,
        )

    try:
        function_result = await middleware_pipeline.execute(middleware_context, final_function_handler)
        return Content.from_function_result(
            call_id=function_call_content.call_id,
            result=function_result,
            additional_properties=function_call_content.additional_properties,
        )
    except MiddlewareTermination as term_exc:
        if middleware_context.result is not None:
            term_exc.result = Content.from_function_result(
                call_id=function_call_content.call_id,
                result=middleware_context.result,
                additional_properties=function_call_content.additional_properties,
            )
        raise
    except UserInputRequiredException:
        raise
    except Exception as exc:
        message = "Error: Function failed."
        if config.get("include_detailed_errors", False):
            message = f"{message} Exception: {exc}"
        return Content.from_function_result(
            call_id=function_call_content.call_id,
            result=message,
            exception=str(exc),
            additional_properties=function_call_content.additional_properties,
        )


def apply_tool_invocation_metadata_patch() -> None:
    """Patch the framework so function middleware can read the OpenAI tool_call_id."""
    import agent_framework._tools as tools_module

    if getattr(tools_module, "_nano_codex_metadata_patch_applied", False):
        return

    tools_module._auto_invoke_function = _patched_auto_invoke_function
    tools_module._nano_codex_metadata_patch_applied = True


__all__ = ["apply_tool_invocation_metadata_patch"]
