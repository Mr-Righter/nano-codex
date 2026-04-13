"""Middleware management system.

This module provides middleware registration and loading capabilities:
- Register middlewares with @register_middleware decorator
- Load middlewares by name using load_middlewares()
- Auto-discover middlewares in the middlewares directory

Usage:
    from middlewares import load_middlewares, register_middleware

    # Load specific middlewares
    middlewares = load_middlewares(["logging_response", "strip_reasoning"])

    # Load all middlewares
    all_middlewares = load_middlewares()

    # Register custom middleware
    @register_middleware("my_middleware")
    @chat_middleware()
    async def my_middleware(context, next):
        await next(context)
"""

from .middleware_registry import register_middleware, load_middlewares, configure_middlewares

# Import all middleware modules to trigger registration
from . import chat_middlewares
from . import function_middlewares
from . import agent_middlewares

__all__ = [
    "register_middleware",
    "load_middlewares",
    "configure_middlewares",
]
