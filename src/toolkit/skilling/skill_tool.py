"""Skill management tools for discovering and using local skills."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Dict, List, Optional

from agent_framework import FunctionTool, tool
from src.utils.markdown_parser import SkillDefinition
from src.utils.plugin_discovery import discover_skill_definitions

from ..tool_loader import register_to_toolkit
from ..tool_support import ToolContext, build_result


SKILL_USAGE_DESCRIPTION_TEMPLATE = """
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke skills using this tool with the skill name only (no arguments)
- When you invoke a skill, you will see the skill's prompt expand
- The skill's prompt will provide detailed instructions on how to complete the task
- Example: use_skill("code-review") - invoke the code-review skill

Important:
- Available skills are listed in <available_skills> or system-reminder messages in the conversation.
- Use this tool with the skill name, for example: use_skill("code-review")
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running


{available_skills}
""".strip()


@register_to_toolkit
class SkillManager:
    """Load local skills and expose them through the ``use_skill`` tool."""

    def __init__(
        self,
        skills_dir: Optional[Path | str] = None,
        hidden_skills: Optional[list[str]] = None,
    ) -> None:
        self.skills_dir = (
            Path(skills_dir)
            if skills_dir is not None
            else Path.cwd() / "configs" / "skills"
        )
        self.available_skills = self.load_skills(hidden_skills)

    def load_skills(
        self, hidden_skills: Optional[list[str]] = None
    ) -> Dict[str, SkillDefinition]:
        skills = discover_skill_definitions(self.skills_dir).values()
        hidden = set(hidden_skills or [])
        available_skills = {}
        for skill in skills:
            if skill.name and skill.name not in hidden and not skill.hidden:
                available_skills[skill.name] = skill
        return available_skills

    async def _use_skill(
        self,
        name: Annotated[str, "The name of the skill to retrieve"],
    ) -> list:
        skill = self.available_skills.get(name)
        if skill is None:
            return build_result(
                f"Skill '{name}' not found. Available skills: {str(list(self.available_skills.keys()))}"
            )

        content = skill.to_xml(
            ["path", "instructions"], root_tag=f"skill_name={skill.name}"
        )
        return build_result(content, display_text=f"Used skill: {skill.name}")

    def build_tools(self, context: ToolContext) -> List[FunctionTool]:
        del context
        if not self.available_skills:
            available_skills_info = (
                "<available_skills><!-- No skills found --></available_skills>"
            )
        else:
            available_skills_info = (
                "<available_skills>\n"
                + "\n".join(
                    skill.to_xml(["name", "description", "invoke_when"], "skill")
                    for skill in self.available_skills.values()
                )
                + "\n</available_skills>"
            )

        description = SKILL_USAGE_DESCRIPTION_TEMPLATE.format(
            available_skills=available_skills_info
        )

        async def use_skill(
            name: Annotated[str, "The name of the skill to retrieve"],
        ) -> list:
            return await self._use_skill(name)

        return [tool(use_skill, name="use_skill", description=description)]

__all__ = [
    "SKILL_USAGE_DESCRIPTION_TEMPLATE",
    "SkillManager",
]
