"""Subagent management tools for discovering and using subagents."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Dict, List, Optional

from agent_framework import FunctionTool, tool
from src.utils.markdown_parser import AgentDefinition
from src.utils.plugin_discovery import discover_agent_definitions

from ..tool_loader import register_to_toolkit
from ..tool_support import ToolContext, build_result

logger = logging.getLogger(__name__)


SUBAGENT_DESCRIPTION_TEMPLATE = """
Launch a new agent to handle complex, multi-step tasks autonomously.

Available agent types:
{available_agent_types}

When using this tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use this tool:
- If you want to read a specific file path, use the Read or Glob tool instead
- If you are searching for a specific class definition, use the Glob tool instead
- If you are searching for code within a specific file or set of 2-3 files, use the Read tool instead
- For simple, straightforward tasks that don't require specialized expertise
- Other tasks that are not related to the agent descriptions above

Usage notes:

1. IMPORTANT: Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you. Be explicit about:
   - The level of detail required (e.g., "provide a brief summary" vs "provide detailed analysis with code examples")
   - The format of the response (e.g., "return a markdown format research report", "write results to a file", "provide a bulleted list")
   - Whether to include supporting evidence, code snippets, file paths, or other context
2. The agent's outputs should generally be trusted
3. Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
4. If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.
""".strip()


@register_to_toolkit
class SubagentManager:
    """Load subagent definitions and expose the delegation tool."""

    def __init__(self, agents_dir: Optional[Path | str] = None):
        self.agents_dir = (
            Path(agents_dir)
            if agents_dir is not None
            else Path.cwd() / "configs" / "agents"
        )
        self.available_agents = self.load_agents()

    def load_agents(self) -> Dict[str, AgentDefinition]:
        agents: Dict[str, AgentDefinition] = {}
        if self.agents_dir.exists():
            agents = discover_agent_definitions(self.agents_dir)
        return agents

    async def _execute(
        self,
        subagent_type: str,
        prompt: str,
        description: str,
        ctx: ToolContext,
    ) -> str:
        agent = self.available_agents.get(subagent_type)
        if agent is None:
            return (
                f"Subagent '{subagent_type}' not found. "
                f"Available agents: {str(list(self.available_agents.keys()))}"
            )

        try:
            from src.core.nano_codex import NanoCodex

            model = agent.model
            if model is None and ctx.chat_client is not None:
                model = ctx.chat_client.model

            agent_config = ctx.config.model_copy(
                update={
                    "model": model,
                    "agent_config_path": str(agent.path),
                    "hidden_skills": agent.hidden_skills,
                }
            )

            assistant = NanoCodex(
                config=agent_config,
                definition=agent,
                middleware=list(ctx.middleware) if ctx.middleware else None,
                ui_sink=ctx.ui_sink,
            )
            async with assistant:
                session = assistant.create_session()
                result = await assistant.run(prompt, session=session)

            text = result.text.strip() if result.text else ""
            if text:
                return text
            return (
                f"Subagent '{subagent_type}' completed the Task({description}) but returned no output."
            )
        except Exception as exc:
            logger.error("Error executing subagent '%s': %s", subagent_type, exc)
            raise RuntimeError(f"Error executing subagent: {exc}") from exc

    def build_tools(self, context: ToolContext) -> List[FunctionTool]:
        if not self.available_agents:
            available_agent_types = (
                "<available_agent_types><!-- No agents found --></available_agent_types>"
            )
        else:
            available_agent_types = (
                "<available_agent_types>\n"
                + "\n".join(
                    agent.to_xml(["name", "description"], "agent")
                    for agent in self.available_agents.values()
                )
                + "\n</available_agent_types>"
            )

        description = SUBAGENT_DESCRIPTION_TEMPLATE.format(
            available_agent_types=available_agent_types
        )

        async def solve_task_with_subagent(
            description: Annotated[str, "A short (3-5 word) description of the task"],
            prompt: Annotated[str, "The task for the agent to perform"],
            subagent_type: Annotated[
                str, "The type of specialized agent to use for this task"
            ],
        ) -> list:
            result = await self._execute(subagent_type, prompt, description, context)
            return build_result(result, display_text=f"Subagent finished: {subagent_type}")

        return [tool(solve_task_with_subagent, description=description)]

    async def refresh(self) -> int:
        self.available_agents = self.load_agents()
        return len(self.available_agents)
