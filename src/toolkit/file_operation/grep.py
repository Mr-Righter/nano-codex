"""Ripgrep-backed search tool for content discovery in local files."""

import asyncio
from typing import Annotated, Literal, Optional

from agent_framework import tool
from ..tool_loader import register_to_toolkit
from ..tool_support import build_result, require_absolute_path


DESCRIPTION = """
Searches for patterns in files using ripgrep (rg).

Usage:
  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`
  - The path parameter is required and must be an absolute file or directory path
""".strip()

@register_to_toolkit
@tool(description=DESCRIPTION)
async def grep(
    pattern: Annotated[
        str,
        "The regular expression pattern to search for in file contents",
    ],
    path: Annotated[
        str,
        "File or directory to search in (rg PATH). This parameter is required.",
    ],
    glob: Annotated[
        Optional[str],
        'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob',
    ] = None,
    output_mode: Annotated[
        Literal["content", "files_with_matches", "count"],
        'Output mode: "content" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), "files_with_matches" shows file paths (supports head_limit), "count" shows match counts (supports head_limit). Defaults to "files_with_matches".',
    ] = "files_with_matches",
    B: Annotated[
        Optional[int],
        'Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.',
    ] = None,
    A: Annotated[
        Optional[int],
        'Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.',
    ] = None,
    C: Annotated[
        Optional[int],
        'Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.',
    ] = None,
    n: Annotated[
        bool,
        'Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise.',
    ] = False,
    i: Annotated[
        bool,
        "Case insensitive search (rg -i)",
    ] = False,
    type: Annotated[
        Optional[str],
        "File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.",
    ] = None,
    head_limit: Annotated[
        Optional[int],
        'Limit output to first N lines/entries, equivalent to "| head -N". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). When unspecified, shows all results from ripgrep.',
    ] = None,
    multiline: Annotated[
        bool,
        "Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.",
    ] = False,
) -> list:
    """Search file contents with ripgrep and normalize the result for the toolkit."""
    _, error = require_absolute_path(path, parameter_name="path")
    if error is not None:
        return build_result(error)

    cmd = ["rg"]
    cmd.append(pattern)
    cmd.append(path)

    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")

    if output_mode == "content":
        if C is not None:
            cmd.extend(["-C", str(C)])
        else:
            if B is not None:
                cmd.extend(["-B", str(B)])
            if A is not None:
                cmd.extend(["-A", str(A)])
        if n:
            cmd.append("-n")

    if i:
        cmd.append("-i")
    if type:
        cmd.extend(["--type", type])
    if glob:
        cmd.extend(["--glob", glob])
    if multiline:
        cmd.extend(["-U", "--multiline-dotall"])

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
    except FileNotFoundError:
        return build_result("ripgrep (rg) is not installed or not in PATH")
    except Exception as e:
        return build_result(f"Error executing ripgrep: {e}")

    output = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    if process.returncode == 1:
        return build_result(f"No matches found for pattern: {pattern}")
    if process.returncode not in (0, 1):
        error_text = stderr or f"ripgrep exited with status {process.returncode}"
        return build_result(f"Error executing ripgrep: {error_text}")

    lines = output.splitlines()
    if head_limit is not None:
        lines = lines[:head_limit]
    if not lines:
        return build_result(f"No matches found for pattern: {pattern}")

    if len(lines) > 50:
        rendered = "\n".join(lines[:50]) + f"\n… (truncated, showing 50/{len(lines)} results)"
    else:
        rendered = "\n".join(lines)
    return build_result(
        rendered,
        display_text=f"Matched lines: {len(lines)}",
    )
