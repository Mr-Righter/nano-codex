"""System prompt assembler for constructing complete agent instructions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import platform

from .markdown_parser import SkillDefinition
from .plugin_discovery import discover_skill_definitions


class SystemPromptAssembler:
    """Assembles complete system prompts with runtime context."""

    def __init__(
        self,
        base_instructions: str | None = None,
        skills_dir: str | None = None,
    ) -> None:
        self.base_instructions = base_instructions
        self.skills_dir = Path(skills_dir) if skills_dir else None
        self._skills_cache: dict[str, SkillDefinition] | None = None

    def _load_skills(self, skill_names: list[str] | None) -> list[SkillDefinition]:
        if not self.skills_dir or not skill_names:
            return []
        if self._skills_cache is None:
            self._skills_cache = discover_skill_definitions(self.skills_dir)

        loaded: list[SkillDefinition] = []
        for skill_name in skill_names:
            skill = self._skills_cache.get(skill_name)
            if skill is None:
                continue
            loaded.append(skill)
        return loaded

    def assemble(
        self,
        work_dir: str | None = None,
        skill_names: list[str] | None = None,
    ) -> str:
        working_directory = work_dir or str(Path.cwd())
        current_platform = platform.system()
        current_date = datetime.now().strftime("%Y-%m-%d")

        components = [self.base_instructions.strip()] if self.base_instructions else []
        components.append(
            "\n\n---\n\n"
            "The following sections provide runtime context for your current execution environment."
        )
        components.append(
            "\n\n<environment>\n"
            f"<working_directory>{working_directory}</working_directory>\n"
            f"<platform>{current_platform}</platform>\n"
            f"<date>{current_date}</date>\n"
            "</environment>"
        )

        skills = self._load_skills(skill_names)
        if skills:
            skill_blocks: list[str] = []
            for skill in skills:
                frontmatter_lines = [
                    "---",
                    f"name: {skill.name}",
                    f"description: {skill.description}",
                ]
                if skill.invoke_when:
                    frontmatter_lines.append(f"invoke_when: {skill.invoke_when}")
                if skill.hidden:
                    frontmatter_lines.append("hidden: true")
                frontmatter_lines.append("---")
                skill_blocks.append(
                    f'<skill name="{skill.name}" path="{skill.path}">\n'
                    + "\n".join(frontmatter_lines)
                    + f"\n\n{skill.instructions}\n"
                    + "</skill>"
                )

            components.append(
                "\n\n<available_skills>\n"
                "<instruction>Skills are pre-loaded for this session. Before starting any implementation work, read the reference files listed inside each skill — they contain up-to-date knowledge that is more reliable than pre-training. Do not skip this step.</instruction>\n"
                + "\n".join(skill_blocks)
                + "\n</available_skills>"
            )

        return "".join(components)


__all__ = ["SystemPromptAssembler"]
