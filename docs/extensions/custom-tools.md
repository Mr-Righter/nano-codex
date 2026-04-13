# Custom Tools

Nano-Codex loads local tools from `src/toolkit/`. A tool is only exposed when it is registered in the toolkit and included in the active agent's `tools:` frontmatter.

## Overview

| Pattern | Use It When | Project Pattern |
| --- | --- | --- |
| Direct function tool | One stateless tool is enough | Small standalone helpers |
| Builder class | The tool needs runtime config, the active client, shared state, or more than one tool | `BashExecutor`, `WebSearchManager`, `SkillManager`, `SubagentManager` |

## How Tool Loading Works

1. Toolkit modules are imported so `@register_to_toolkit` runs.
2. The registry stores either a `FunctionTool` or a builder class.
3. `load_tools(...)` materializes local tools for the current runtime and can append configured MCP tools.
4. The active agent definition filters the final tool surface by tool name.

This means a tool is not available just because it exists in `src/toolkit/`. It must also be enabled in `agent.md` or a subagent definition.

## ToolContext

`ToolContext` is the runtime object shared with builder classes. It carries the current config plus runtime-bound services such as:

- `work_dir`, `model_config_path`, `mcp_config_path`
- `skills_dir`, `agents_dir`, `bash_envs`
- `chat_client`, `ui_sink`, `middleware`
- `hidden_skills`

Builder constructors can request these values by parameter name. `tool_loader.py` matches constructor parameters against `ToolContext` automatically.

## Minimal Function Tool

Use a direct function tool when the behavior is simple and does not need runtime rebinding.

```python
from typing import Annotated

from agent_framework import tool

from src.toolkit.tool_loader import register_to_toolkit
from src.toolkit.tool_support import build_result


@register_to_toolkit
@tool(description="Return a short greeting.")
async def hello(
    name: Annotated[str, "The person to greet"],
) -> list:
    return build_result(
        f"Hello, {name}!",
        display_text=f"Greeted {name}",
    )
```

## Builder Class Tool

Use a builder class when the tool needs runtime data or when one manager should expose multiple tools.

```python
from typing import Annotated

from agent_framework import FunctionTool, tool

from src.toolkit.tool_loader import register_to_toolkit
from src.toolkit.tool_support import ToolContext, build_result


@register_to_toolkit
class ExampleManager:
    def __init__(self, context: ToolContext, work_dir: str | None = None) -> None:
        self._context = context
        self._work_dir = work_dir or context.work_dir

    async def describe_workdir(self) -> list:
        return build_result(
            f"Current work directory: {self._work_dir}",
            display_text="Described work directory",
        )

    async def echo(
        self,
        message: Annotated[str, "Message to echo back"],
    ) -> list:
        return build_result(message, display_text="Echoed message")

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        del context
        return [
            tool(
                self.describe_workdir,
                name="describe_workdir",
                description="Describe the current work directory.",
            ),
            tool(
                self.echo,
                name="echo",
                description="Echo one string.",
            ),
        ]
```

One builder can expose multiple tools. This is the right pattern when related tools share the same runtime client, configuration, or lifecycle.

## Project Pattern

- Use direct function tools for small stateless helpers.
- Use builder classes for runtime-bound tools.
- Return results through `build_result(...)` so console and TUI presenters get a clean summary.
- Keep tool names stable; agent filtering happens by name.
- Reuse `ToolContext.chat_client` when a tool needs the active model.
