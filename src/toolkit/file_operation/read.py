"""Plain-text file reader for local source and config inspection."""

from pathlib import Path
from typing import Annotated

from agent_framework import tool

from ..tool_loader import register_to_toolkit
from ..tool_support import build_result, require_absolute_path

DESCRIPTION = """
Reads a plain text file from the local filesystem.

Usage:
  - The file_path parameter must be an absolute path, not a relative path
  - By default, it reads up to 2000 lines starting from the beginning of the file
  - When the end of file is reached, an [EOF] marker will be appended to indicate no more content is available
  - You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
  - Any lines longer than 2000 characters will be truncated
  - Results are returned using cat -n format, with line numbers starting at 1
  - You have the capability to call multiple tools in a single response. When you need to read multiple files, it is better to send multiple function calls at once in a batch rather than reading them one by one.
  - If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
""".strip()


@register_to_toolkit
@tool(description=DESCRIPTION)
async def read(
    file_path: Annotated[
        str,
        "The absolute path to the file to read.",
    ],
    offset: Annotated[
        int,
        "The line number to start reading from (1-indexed). Only provide if the file is too large to read at once.",
    ] = 1,
    limit: Annotated[
        int,
        "Number of lines to read. Only provide if the file is too large to read at once.",
    ] = 2000,
) -> list:
    """Read a text file slice and return it with line numbers."""
    p, error = require_absolute_path(file_path, parameter_name="file_path")
    if error is not None:
        return build_result(error)
    assert p is not None

    if not p.exists():
        return build_result(f"File not found: {file_path}")
    if not p.is_file():
        return build_result(f"Path is not a regular file: {file_path}")
    if p.stat().st_size == 0:
        return build_result(f"File is empty: {file_path}")

    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        subset = lines[offset - 1 : offset - 1 + limit]
        formatted = [f"{i+offset:>6}\t{line[:2000]}" for i, line in enumerate(subset)]
        end_line = offset + len(subset) - 1

        if end_line == total_lines:
            formatted.append("[EOF]")

        if end_line == total_lines and offset == 1:
            user_msg = f"Read file: {p.name} (lines 1-{end_line} of {total_lines} total)"
        elif end_line == total_lines:
            user_msg = f"Read file: {p.name} (lines {offset}-{end_line} of {total_lines} total)"
        else:
            user_msg = f"Read file: {p.name} (lines {offset}-{end_line} of {total_lines} total, more available)"

        content = "\n".join(formatted)
        return build_result(content, display_text=user_msg)

    except Exception as e:
        return build_result(f"File is not a readable plain text file: {e}")
