"""String-replacement tool for precise edits to existing files."""

from pathlib import Path
from typing import Annotated

from agent_framework import tool

from ..tool_loader import register_to_toolkit
from ..tool_support import build_result, require_absolute_path


DESCRIPTION = """
Perform precise, surgical string replacements in files with exact matching.

Usage:
  - When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: spaces + line number + tab. Everything after that tab is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
  - ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
  - The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
  - Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance.
""".strip()


@register_to_toolkit
@tool(description=DESCRIPTION)
async def edit(
    file_path: Annotated[
        str,
        "The absolute path to the file to modify",
    ],
    old_string: Annotated[
        str,
        "The text to replace",
    ],
    new_string: Annotated[
        str,
        "The text to replace it with (must be different from old_string)",
    ],
    replace_all: Annotated[
        bool,
        "Replace all occurences of old_string (default false)",
    ] = False,
) -> list:
    """Perform an exact text replacement in one existing file."""
    p, error = require_absolute_path(file_path, parameter_name="file_path")
    if error is not None:
        return build_result(error)
    assert p is not None

    if not p.exists():
        return build_result(f"File not found: {file_path}")
    if not p.is_file():
        return build_result(f"Path is not a regular file: {file_path}")
    if old_string == new_string:
        return build_result("old_string and new_string must be different")

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return build_result(f"Failed to read file: {e}")

    if old_string not in content:
        return build_result(f"String not found in file: {old_string!r}")

    occurrence_count = content.count(old_string)
    if not replace_all and occurrence_count > 1:
        return build_result(
            f"The string appears {occurrence_count} times in the file. "
            f"Either provide a larger string with more surrounding context to make it unique, "
            f"or set replace_all=True to replace all occurrences."
        )

    line_ranges = []
    start_pos = 0
    new_line_count = new_string.count("\n")

    for _ in range(occurrence_count if replace_all else 1):
        pos = content.find(old_string, start_pos)
        if pos == -1:
            break
        start_line = content[:pos].count("\n") + 1
        line_ranges.append((start_line, start_line + new_line_count))
        start_pos = pos + len(old_string)

    count = occurrence_count if replace_all else 1
    new_content = content.replace(old_string, new_string, count)

    try:
        p.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return build_result(f"Failed to write file: {e}")

    def format_range(start_line: int, end_line: int) -> str:
        return str(start_line) if start_line == end_line else f"{start_line}-{end_line}"

    ranges_str = ", ".join(format_range(s, e) for s, e in line_ranges[:3])

    if len(line_ranges) > 3:
        ranges_str += f"... (+{len(line_ranges) - 3} more)"

    line_word = (
        "line"
        if len(line_ranges) == 1 and line_ranges[0][0] == line_ranges[0][1]
        else "lines"
    )

    return build_result(
        f"Edited file: {file_path} (replaced {count} occurrence(s) at {line_word} {ranges_str})",
        display_text=f"Edited file: {p.name}",
    )
