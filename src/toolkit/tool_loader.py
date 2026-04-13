"""Tool loading and builder registration for the toolkit."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Protocol, TypeAlias, Union, runtime_checkable

from agent_framework import FunctionTool, MCPStdioTool, MCPStreamableHTTPTool, MCPWebsocketTool
from .tool_support import ToolContext

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolBuilder(Protocol):
    def build_tools(self, context: ToolContext) -> List[FunctionTool]:
        ...


ToolkitEntry: TypeAlias = FunctionTool | type[ToolBuilder]

TOOLKIT: list[ToolkitEntry] = []


def register_to_toolkit(entry: ToolkitEntry) -> ToolkitEntry:
    """Register one toolkit FunctionTool or builder class."""

    is_function_tool = isinstance(entry, FunctionTool)
    is_builder_class = inspect.isclass(entry) and callable(getattr(entry, "build_tools", None))
    if not is_function_tool and not is_builder_class:
        raise TypeError(
            "register_to_toolkit only supports FunctionTool instances or builder classes"
        )
    if entry not in TOOLKIT:
        TOOLKIT.append(entry)
    return entry


def _build_init_kwargs(builder_cls: type[ToolBuilder], context: ToolContext) -> dict[str, Any]:
    """Resolve ``__init__`` arguments for one builder from the runtime ToolContext."""

    kwargs: dict[str, Any] = {}
    signature = inspect.signature(builder_cls.__init__)
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.name == "context":
            kwargs["context"] = context
            continue
        if hasattr(context, parameter.name):
            kwargs[parameter.name] = getattr(context, parameter.name)
            continue
        if parameter.default is inspect.Parameter.empty:
            raise TypeError(
                f"{builder_cls.__name__}.__init__() requires unsupported parameter "
                f"'{parameter.name}' that is not available on ToolContext"
            )
    return kwargs


def _load_registered_tools(context: ToolContext) -> List[FunctionTool]:
    """Materialize all decorator-registered toolkit tools for one runtime."""

    tools: list[FunctionTool] = []
    for entry in TOOLKIT:
        if isinstance(entry, FunctionTool):
            tools.append(entry)
            continue
        if inspect.isclass(entry):
            tools.extend(entry(**_build_init_kwargs(entry, context)).build_tools(context))
            continue
        raise TypeError(f"Unsupported toolkit entry: {entry!r}")
    return tools


def _load_mcp_tools(
    config_path: Optional[Union[str, Path]],
    service_names: Optional[list[str]] = None,
) -> List[Any]:
    """Construct MCP tools from a config file, filtered by configured services."""

    if config_path is None or not service_names:
        return []

    path = Path(config_path)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.error("Failed to read MCP config %s: %s", path, exc)
        return []

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        logger.error("Invalid MCP config: missing 'mcpServers' key in %s", path)
        return []

    missing = sorted(set(service_names) - set(servers))
    if missing:
        raise ValueError(f"Unknown MCP services: {str(missing)}")

    tools: List[Any] = []
    for name in service_names:
        cfg = servers[name]
        try:
            tools.append(_create_mcp_tool(name, cfg))
        except Exception as exc:
            logger.error("Failed to load MCP '%s': %s", name, exc)
            raise
    return tools


def _create_mcp_tool(name: str, cfg: dict) -> Any:
    common = {
        "name": name,
        "description": cfg.get("description"),
        "approval_mode": cfg.get("approval_mode"),
        "allowed_tools": cfg.get("allowed_tools"),
        "load_tools": cfg.get("load_tools", True),
        "load_prompts": cfg.get("load_prompts", True),
        "request_timeout": cfg.get("request_timeout"),
    }
    server_type = cfg.get("type", "stdio").lower()
    if server_type == "stdio":
        cmd = cfg.get("command")
        if not cmd:
            raise ValueError(f"MCP stdio '{name}' missing 'command'")
        return MCPStdioTool(command=cmd, args=cfg.get("args", []), env=cfg.get("env"), **common)
    if server_type in ("http", "streamable_http", "sse"):
        url = cfg.get("url")
        if not url:
            raise ValueError(f"MCP HTTP '{name}' missing 'url'")
        return MCPStreamableHTTPTool(url=url, **common)
    if server_type in ("websocket", "ws"):
        url = cfg.get("url")
        if not url:
            raise ValueError(f"MCP WebSocket '{name}' missing 'url'")
        return MCPWebsocketTool(url=url, **common)
    raise ValueError(f"Unknown MCP type '{server_type}' for '{name}'")


def load_tools(
    context: Optional[ToolContext] = None,
    *,
    tool_names: Optional[List[str]] = None,
    enable_mcp: bool = True,
    mcp_config_path: Optional[Union[str, Path]] = None,
    mcp_services: Optional[List[str]] = None,
) -> List[Any]:
    """Load and return all tools for one agent runtime."""

    ctx = context or ToolContext()
    all_tools: List[Any] = []

    all_tools.extend(_load_registered_tools(ctx))

    if enable_mcp and mcp_services:
        cfg_path = mcp_config_path or ctx.mcp_config_path
        all_tools.extend(_load_mcp_tools(cfg_path, mcp_services))

    if tool_names is not None:
        name_set = set(tool_names)
        all_tools = [tool for tool in all_tools if getattr(tool, "name", None) in name_set]

    return all_tools


__all__ = [
    "ToolBuilder",
    "ToolContext",
    "load_tools",
]
