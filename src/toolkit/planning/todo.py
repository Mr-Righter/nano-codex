"""Session-scoped todo tracking tool used by the runtime prompt workflow."""

from typing import Annotated, List, Literal, Sequence

from agent_framework import tool
from pydantic import BaseModel, Field

from ..tool_loader import register_to_toolkit
from ..tool_support import build_result

DESCRIPTION = """
Use this tool to create and manage a **structured task list for your current coding session**.
This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:
- Complex multi-step tasks requiring 3 or more distinct steps or actions
- Non-trivial tasks requiring careful planning or multiple operations
- User explicitly requests todo list
- User provides multiple tasks (numbered or comma-separated lists)
- After receiving new instructions - immediately capture user requirements as todos
- Before starting work on a task - mark it as in_progress BEFORE beginning work. **Ideally you should only have one todo as in_progress at a time**
- After completing a task - mark it as completed IMMEDIATELY and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE: You should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Management Rules

### Task States
- **pending**: Task not yet started
- **in_progress**: Currently working on (limit to ONE task at a time)
- **completed**: Task finished successfully

### Critical Guidelines
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (do not batch completions)
- Have at most ONE task in_progress at any time
- Complete current tasks before starting new ones
- Remove tasks that are no longer relevant from the list entirely

### Task Completion Requirements
- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved.
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

### Task Breakdown
- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Always provide both forms:
  - content: Imperative form (e.g., "Fix authentication bug")
  - activeForm: Present continuous form (e.g., "Fixing authentication bug")

When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully.
""".strip()


class TodoItem(BaseModel):
    """Represents a single todo item with content, status, and active form."""

    content: str = Field(
        description="Imperative form: what needs to be done", min_length=1
    )
    status: Literal["pending", "in_progress", "completed"] = Field(
        description="Task status"
    )
    activeForm: str = Field(
        description="Present continuous form: what's being done", min_length=1
    )


# Module-level todo state
_todos: List[TodoItem] = []


@register_to_toolkit
@tool(description=DESCRIPTION)
async def write_todos(
    todos: Annotated[Sequence[TodoItem], "List of todo items to write"]
) -> list:
    """Replace the current todo list with a validated session-scoped snapshot."""
    global _todos

    # Phase 1: normalize tool arguments into TodoItem objects.
    normalized: List[TodoItem] = []
    for item in todos:
        if isinstance(item, dict):
            normalized.append(TodoItem(**item))
        elif isinstance(item, TodoItem):
            normalized.append(item)
        else:
            raise ValueError(f"Invalid todo type: {type(item)}")

    # Phase 2: enforce the single in-progress invariant expected by the prompt rules.
    in_progress_count = sum(1 for t in normalized if t.status == "in_progress")
    if in_progress_count > 1:
        return build_result(f"Expected at most 1 in_progress todo, but found {in_progress_count}")

    # Phase 3: swap the module-level state and render the updated progress summary.
    _todos = normalized

    completed = sum(1 for t in _todos if t.status == "completed")
    in_progress_item = next((t for t in _todos if t.status == "in_progress"), None)
    total = len(_todos)

    header = f"Tasks: {completed}/{total} ✔"
    if in_progress_item:
        header += f"  |  Now: {in_progress_item.activeForm}"

    if not _todos:
        progress_text = "No todos."
    else:
        progress_lines = []
        for index, todo in enumerate(_todos, start=1):
            if todo.status == "completed":
                marker = "✔"
                text = todo.content
            elif todo.status == "in_progress":
                marker = "⊙"
                text = todo.activeForm
            else:
                marker = "○"
                text = todo.content
            progress_lines.append(f"{index}. {marker} {text}")
        progress_text = "\n".join(progress_lines)

    return build_result(header + "\n\n" + progress_text, display_text=header)
