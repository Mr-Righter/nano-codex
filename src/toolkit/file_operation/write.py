"""File-writing tool for creating or overwriting one local file."""

from pathlib import Path
from typing import Annotated

from agent_framework import tool

from ..tool_loader import register_to_toolkit
from ..tool_support import build_result, require_absolute_path


DESCRIPTION = """
Writes a file to the local filesystem.

Usage:
  - This tool will overwrite the existing file if there is one at the provided path.
  - ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
  - Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
""".strip()

@register_to_toolkit
@tool(description=DESCRIPTION)
async def write(
    file_path: Annotated[
        str, "The absolute path to the file to write (must be absolute, not relative)"
    ],
    content: Annotated[str, "Full content to write"],
) -> list:
    """Write one file from scratch or overwrite its entire contents."""
    try:
        p, error = require_absolute_path(file_path, parameter_name="file_path")
        if error is not None:
            return build_result(error)
        assert p is not None

        file_exists = p.exists()
        if file_exists and not p.is_file():
            return build_result(f"Path is not a regular file: {file_path}")

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        line_count = len(content.splitlines())

        if file_exists:
            return build_result(
                f"Wrote file: {file_path} ({line_count} lines). Existing content was overwritten.",
                display_text=f"Wrote file: {p.name}",
            )
        return build_result(
            f"Wrote file: {file_path} ({line_count} lines)",
            display_text=f"Wrote file: {p.name}",
        )
    except Exception as e:
        return build_result(f"Failed to write file: {e}")
