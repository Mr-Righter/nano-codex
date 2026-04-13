"""Development log tool for recording durable project milestones and debugging notes."""

from typing import Annotated, List, Optional
import logging
from pathlib import Path
import re
from datetime import datetime
from pydantic import Field

from agent_framework import FunctionTool, tool
from ..tool_loader import register_to_toolkit
from ..tool_support import ToolContext, build_result

logger = logging.getLogger(__name__)

DESCRIPTION = """
Use this tool to create and manage a **comprehensive development log for your entire project session**.
This helps you maintain a historical record of the development process, track major milestones, document debugging efforts, and provide a clear narrative of project evolution.

## When to Use This Tool

Use this tool proactively in these scenarios:

1. **Project Initialization** - When starting a new project or major task to establish the project mission
2. **Milestone Achievements** - After completing a significant feature, phase, or major refactoring
3. **Bug Resolution** - After successfully debugging and fixing issues to document the process
4. **Progress Checkpoints** - At natural breakpoints to create a snapshot of development progress
5. **Structure Changes** - After significant codebase reorganization
6. **User Request to Save Progress** - When user explicitly asks to save/preserve/checkpoint current work state

## When NOT to Use This Tool

Skip using this tool when:
- Making trivial updates that don't represent meaningful progress (e.g., fixing typos)
- Still in exploratory phase without concrete progress to report
- The change is too minor to warrant historical logging
- You're just reading or analyzing code without making changes

NOTE: This tool creates a **persistent historical record**. Only log meaningful progress that you'd want to refer back to later.

## Update Strategies

- **OVERWRITE**: Replaces previous content (Project Mission, Current Todos)
- **APPEND**: Adds timestamped entries to history (Milestones, Debugging Log) - only pass NEW content

## Log Sections

### 1. Project Mission (OVERWRITE)
High-level project goal and scope. Update when:
- Initial task description received
- Requirements change or scope expands

**Must include ALL user requirements comprehensively.**

### 2. Project Structure (OVERWRITE)
Codebase layout and architecture overview for quick context recovery.

**When to Update:**
- After project initialization/scaffolding
- After significant structural changes (new modules, directory reorganization)

**Content:**
- Directory tree (key folders only, with brief annotations)
- Tech stack summary (e.g., "Python + FastAPI + PostgreSQL")
- Key entry points (e.g., "CLI entry: launcher.py")

**Exclude:** node_modules, build outputs, individual files (keep high-level)

### 3. Milestones (APPEND)
Major achievements and progress markers. Log when completing:
- Major features or subsystems
- Significant refactoring
- Key deliverables or quality milestones

Be specific about accomplishments. Use bullet points for related achievements.

### 4. Debugging Log (APPEND)
Document problems and solutions for future reference.

**Structure each entry:**
- Problem (symptoms)
- Root Cause
- Solution
- Outcome/Learnings

## Best Practices

1. Always set `project_mission` first when beginning work
2. Log milestones regularly to show accomplishments
3. Be specific - avoid vague entries like "made progress"
4. Write entries useful to read weeks or months later
5. Update only changed sections - not all sections every time
6. When user requests to save/preserve progress, immediately snapshot current state

When in doubt, log it. A well-maintained development log is invaluable for tracking progress, debugging issues, and understanding project evolution.
""".strip()


@register_to_toolkit
class DevLogManager:
    """Manages development logs with persistent file storage."""

    def __init__(
        self,
        work_dir: Optional[str] = None,
        dev_log_name: str = "dev_log.md",
    ):
        """Initialize DevLogManager.

        Args:
            work_dir: Directory where the dev log will be stored. Defaults to current working directory.
            dev_log_name: Name of the dev log file. Defaults to "dev_log.md".
        """
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.dev_log_name = dev_log_name
        self.log_path = self.work_dir / dev_log_name
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _read_current_log(self) -> dict:
        """Read and parse the current log file.

        Returns:
            dict with keys: project_mission, project_structure, milestones, debugging_log
        """
        if not self.log_path.exists():
            return {
                "project_mission": "",
                "project_structure": "",
                "milestones": "",
                "debugging_log": "",
            }

        content = self.log_path.read_text(encoding="utf-8")

        def extract_section(tag: str, content: str) -> str:
            """Extract content between XML tags."""
            pattern = f"<{tag}>(.*?)</{tag}>"
            match = re.search(pattern, content, re.DOTALL)
            if match:
                # Remove leading/trailing whitespace but preserve internal formatting
                return match.group(1).strip()
            return ""

        return {
            "project_mission": extract_section("project_mission", content),
            "project_structure": extract_section("project_structure", content),
            "milestones": extract_section("milestones", content),
            "debugging_log": extract_section("debugging_log", content),
        }

    def _write_log(
        self,
        project_mission: Optional[str],
        project_structure: Optional[str],
        milestones: Optional[str],
        debugging_log: Optional[str],
    ) -> List[str]:
        """Write the complete log file with selective updates.

        Args:
            project_mission: New project mission (None = keep existing)
            project_structure: New project structure (None = keep existing)
            milestones: New milestone to append (None = no change)
            debugging_log: New debug entry to append (None = no change)

        Returns:
            List of updated section names
        """
        current = self._read_current_log()
        updated_sections = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Phase 1: update overwrite-style sections that should always reflect the latest state.
        if project_mission is not None:
            current["project_mission"] = (
                f"<!-- Updated: {timestamp} -->\n{project_mission.strip()}"
            )
            updated_sections.append("Project Mission")

        # Update project structure (overwrite if provided)
        if project_structure is not None:
            current["project_structure"] = (
                f"<!-- Updated: {timestamp} -->\n{project_structure.strip()}"
            )
            updated_sections.append("Project Structure")

        # Phase 2: append timestamped history entries for milestones and debugging notes.
        if milestones is not None:
            milestone_entry = f"\n\n## {timestamp}\n{milestones.strip()}"
            current["milestones"] = (
                current["milestones"] + milestone_entry
                if current["milestones"]
                else milestone_entry.strip()
            )
            updated_sections.append("Milestones")

        # Append debugging log (with timestamp header)
        if debugging_log is not None:
            debug_entry = f"\n\n## {timestamp}\n{debugging_log.strip()}"
            current["debugging_log"] = (
                current["debugging_log"] + debug_entry
                if current["debugging_log"]
                else debug_entry.strip()
            )
            updated_sections.append("Debugging Log")

        # Phase 3: serialize the whole log in the XML-wrapped markdown layout.
        md_content = f"""# Development Log

<project_mission>
{current["project_mission"]}
</project_mission>

<project_structure>
{current["project_structure"]}
</project_structure>

<milestones>
{current["milestones"]}
</milestones>

<debugging_log>
{current["debugging_log"]}
</debugging_log>
"""

        # Write to file
        self.log_path.write_text(md_content, encoding="utf-8")

        return updated_sections

    async def write_dev_log(
        self,
        project_mission: Annotated[
            Optional[str],
            Field(
                description="High-level project goal and scope. Only provide when starting new task or when mission significantly changes. This OVERWRITES the previous mission."
            ),
        ] = None,
        project_structure: Annotated[
            Optional[str],
            Field(
                description="Codebase layout and architecture overview. Provide after project initialization or significant structural changes. This OVERWRITES the previous structure."
            ),
        ] = None,
        milestones: Annotated[
            Optional[str],
            Field(
                description="NEW milestone(s) just achieved. Only provide current accomplishment(s) - will be appended to history with timestamp. Use bullet points for multiple related items."
            ),
        ] = None,
        debugging_log: Annotated[
            Optional[str],
            Field(
                description="NEW debugging entry for the issue just resolved. Only provide current debug record - will be appended to history with timestamp. Structure: Problem, Root Cause, Solution, Outcome."
            ),
        ] = None,
    ) -> list:
        """Create or update the persistent development log file."""
        # Phase 1: reject empty updates so the tool always records meaningful progress.
        if all(
            x is None
            for x in [
                project_mission,
                project_structure,
                milestones,
                debugging_log,
            ]
        ):
            return build_result("No updates provided. Please provide at least one section to update.")

        try:
            # Phase 2: write the file and tailor the response for create vs update flows.
            is_new_log = not self.log_path.exists()

            updated = self._write_log(
                project_mission=project_mission,
                project_structure=project_structure,
                milestones=milestones,
                debugging_log=debugging_log,
            )

            action = "created" if is_new_log else "updated"
            section_verb = "Initialized" if is_new_log else "Updated"
            result = f"Development log {action}: {self.log_path}\n"
            result += f"{section_verb} sections: {str(updated)}"

            display_text = (
                f"Wrote development log: {self.log_path.name}"
                if is_new_log
                else f"Updated development log: {self.log_path.name}"
            )
            return build_result(result, display_text=display_text)

        except Exception as e:
            logger.error(f"Failed to write dev log: {e}")
            # Use the current file presence to report the failed action accurately.
            action = "create" if not self.log_path.exists() else "update"
            return build_result(f"Failed to {action} development log: {str(e)}")

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        """Return FunctionTool instance for write_dev_log."""
        del context
        return [tool(self.write_dev_log, description=DESCRIPTION)]
