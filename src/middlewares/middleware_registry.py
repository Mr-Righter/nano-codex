"""Middleware registration and loading system.

This module provides automatic middleware discovery and loading:
- Middlewares are registered via @register_middleware decorator
- Supports agent_framework middleware types (agent, function, chat)
- Load middlewares by name or load all at once

Examples:
    >>> @register_middleware("logging_response")
    ... @chat_middleware()
    ... async def logging_response_middleware(context: ChatContext, next):
    ...     await next(context)
    ...     return context.result

    >>> middlewares = load_middlewares(["logging_response", "strip_reasoning"])
    >>> all_middlewares = load_middlewares()
"""

import inspect
from typing import Callable, Dict, List, Optional, Sequence, Union

from agent_framework import (
    AgentMiddleware,
    ChatMiddleware,
    FunctionMiddleware,
)
from src.ui.protocol import UiEventSink

MiddlewareType = Union[Callable, FunctionMiddleware, AgentMiddleware, ChatMiddleware]
RegisteredMiddleware = MiddlewareType | type[FunctionMiddleware] | type[AgentMiddleware] | type[ChatMiddleware]

# ==================== REGISTRY ====================

# Middleware registry: name -> middleware function, class, or instance
_MIDDLEWARE_REGISTRY: Dict[str, RegisteredMiddleware] = {}


# ==================== REGISTRATION API ====================


def register_middleware(name: str) -> Callable[[RegisteredMiddleware], RegisteredMiddleware]:
    """Decorator/registrar for middleware functions or class instances.

    Supports both decorated async functions and FunctionMiddleware/AgentMiddleware/
    ChatMiddleware class instances.

    Examples:
        >>> @register_middleware("my_middleware")
        ... @function_middleware
        ... async def my_middleware(context, call_next):
        ...     ...

        >>> register_middleware("my_middleware")(MyMiddleware())
    """

    def decorator(obj: RegisteredMiddleware) -> RegisteredMiddleware:
        if name in _MIDDLEWARE_REGISTRY:
            raise ValueError(f"Middleware '{name}' is already registered")

        _MIDDLEWARE_REGISTRY[name] = obj
        return obj

    return decorator


# ==================== LOADING API ====================


def load_middlewares(names: Optional[Union[str, List[str]]] = None) -> List[RegisteredMiddleware]:
    """Load middleware functions by name.

    Args:
        names: Optional middleware name(s) to load. If None, loads all registered middlewares.

    Returns:
        List of middleware functions

    Raises:
        ValueError: If a requested middleware name is not found

    Examples:
        >>> all_middlewares = load_middlewares()
        >>> middlewares = load_middlewares(["logging_response", "strip_reasoning"])
        >>> middleware = load_middlewares("logging_response")
    """
    if names is None:
        return list(_MIDDLEWARE_REGISTRY.values())

    if isinstance(names, str):
        names = [names]

    middlewares: list[RegisteredMiddleware] = []
    for name in names:
        if name not in _MIDDLEWARE_REGISTRY:
            raise ValueError(f"Middleware '{name}' not found in registry, available: {list(_MIDDLEWARE_REGISTRY.keys())}")
        middlewares.append(_MIDDLEWARE_REGISTRY[name])

    return middlewares


def configure_middlewares(
    middleware: Sequence[RegisteredMiddleware] | None,
    *,
    ui_sink: UiEventSink | None = None,
) -> list[MiddlewareType] | None:
    """Instantiate class-based middleware so each agent gets isolated state.

    Examples:
        >>> configured = configure_middlewares(
        ...     load_middlewares(["logging_response", "tool_result_reminder"]),
        ...     ui_sink=my_ui_sink,
        ... )

    Function-style middleware is passed through unchanged. Class-based middleware
    is instantiated or cloned so each agent run gets its own stateful instance.
    """
    base_items = list(middleware or [])

    def build_kwargs(factory: Callable[..., object]) -> dict[str, object]:
        signature = inspect.signature(factory)
        kwargs: dict[str, object] = {}
        if "ui_sink" in signature.parameters:
            kwargs["ui_sink"] = ui_sink
        return kwargs

    def instantiate(item: RegisteredMiddleware) -> MiddlewareType:
        if inspect.isclass(item) and issubclass(item, (FunctionMiddleware, AgentMiddleware, ChatMiddleware)):
            return item(**build_kwargs(item))
        if isinstance(item, (FunctionMiddleware, AgentMiddleware, ChatMiddleware)):
            clone = getattr(item, "clone", None)
            if callable(clone):
                return clone(**build_kwargs(clone))
            try:
                return type(item)(**build_kwargs(type(item)))
            except TypeError:
                return item
        return item

    configured: list[MiddlewareType] = []
    # Build a per-agent middleware list so stateful middleware does not leak across runs.
    for item in base_items:
        configured.append(instantiate(item))

    return configured or None


__all__ = [
    "register_middleware",
    "load_middlewares",
    "configure_middlewares",
]
