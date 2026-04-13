"""Toolkit modules for Nano-Codex.

Usage:
    from src.core.nano_codex import NanoCodexConfig
    from src.toolkit import load_tools, ToolContext

    context = ToolContext(
        config=NanoCodexConfig(work_dir="/project"),
        chat_client=client,
    )
    tools = load_tools(context)
    agent = client.as_agent(tools=tools)
    await agent.run("task")
"""

from .tool_support import ToolContext
from .tool_loader import ToolBuilder, load_tools

# Import toolkit subpackages so their decorators register tools eagerly.
from . import bash, file_operation, planning, skilling, subagent, web_operation

__all__ = [
    "load_tools",
    "ToolContext",
    "ToolBuilder",
]
