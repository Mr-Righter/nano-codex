"""Filesystem glob tool for file discovery without shelling out to find."""

from pathlib import Path
from typing import Annotated

from agent_framework import tool
from wcmatch import glob as wcglob
from ..tool_loader import register_to_toolkit
from ..tool_support import build_result, require_absolute_path


DESCRIPTION = """
Fast file pattern matching tool that works with any codebase size.

Usage:
  - Supports glob patterns with brace expansion and recursive search using wcmatch
  - Pattern examples: "**/*.js", "src/**/*.ts", "**/*.{js,ts,tsx}", "src/**/test_*.py"
  - Returns matching file paths sorted by modification time
  - Use this tool when you need to find files by name patterns
  - You have the capability to call multiple tools in a single response. When you need to perform multiple searches, it is better to send multiple function calls at once in a batch rather than searching one by one.
  - The path parameter is required and must be an absolute directory path
""".strip()

@register_to_toolkit
@tool(description=DESCRIPTION)
async def glob(
    pattern: Annotated[
        str,
        "The glob pattern to match files against",
    ],
    path: Annotated[
        str,
        "The directory to search in. This parameter is required.",
    ],
) -> list:
    """Match files under one absolute directory using glob patterns."""
    p, error = require_absolute_path(path, parameter_name="path")
    if error is not None:
        return build_result(error)
    assert p is not None

    if not p.exists():
        return build_result(f"Directory not found: {path}")
    if not p.is_dir():
        return build_result(f"Path is not a directory: {path}")

    try:
        matches = wcglob.glob(
            pattern,
            root_dir=str(p),
            flags=wcglob.GLOBSTAR | wcglob.BRACE,
        )
        match_paths = [(p / match) for match in matches]
        sorted_matches = sorted(
            (match_path for match_path in match_paths if match_path.is_file()),
            key=lambda match_path: match_path.stat().st_mtime,
            reverse=True,
        )
    except Exception as e:
        return build_result(f"Failed to search for files matching pattern '{pattern}': {e}")

    lines = [str(match) for match in sorted_matches]
    if not lines:
        return build_result(f"No files matched pattern: {pattern} in {path}")
    if len(lines) > 50:
        return build_result(
            "\n".join(lines[:50]) + f"\n… (truncated, showing 50/{len(lines)} results)",
            display_text=f"Matched files: {len(lines)}",
        )
    return build_result(
        "\n".join(lines),
        display_text=f"Matched files: {len(lines)}",
    )
