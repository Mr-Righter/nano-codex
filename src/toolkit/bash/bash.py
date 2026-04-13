"""Toolkit builder for foreground/background bash execution."""

import asyncio
import html
import logging
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from agent_framework import FunctionTool, tool
logger = logging.getLogger(__name__)

from ..tool_loader import register_to_toolkit
from ..tool_support import ToolContext, build_result
from .persistent_shell import PersistentShellSession


@dataclass
class BackgroundShell:
    """Bookkeeping for one long-running background shell process."""

    bash_id: str
    command: str
    process: asyncio.subprocess.Process
    output_buffer: str
    read_position: int
    output_task: asyncio.Task  # async task for collecting output


ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
INTERACTIVE_COMMAND_PATTERN = re.compile(
    r"^(read|sudo|passwd|ssh|sftp|ftp|top|htop|less|more|man|vim|nvim|nano)$"
)
SYSTEM_INSTALL_NONINTERACTIVE_FLAGS = {
    "apt": {"-y", "--yes", "--assume-yes"},
    "apt-get": {"-y", "--yes", "--assume-yes"},
    "yum": {"-y", "--assumeyes"},
    "dnf": {"-y", "--assumeyes"},
}


def _normalize_bash_envs(raw_envs: dict[str, str] | None) -> dict[str, str]:
    """Validate and normalize task-configured bash environment variables."""
    if raw_envs is None:
        return {}

    env_vars: dict[str, str] = {}
    for key, value in raw_envs.items():
        if key is None or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise ValueError(f"Invalid bash environment variable name: {key!r}")
        if not isinstance(value, str):
            raise ValueError(
                f"Invalid bash environment value for {key!r}: expected string, got {type(value).__name__}"
            )
        env_vars[key] = value

    return env_vars

BASH_DESCRIPTION = """
Executes a bash command in either a persistent shell session (default) or as a background process.

**State Persistence (Default Mode)**:
  - By default, commands run in a persistent shell session
  - Working directory (cd) and environment variables (export) are maintained across calls
  - All output is returned in XML format with status, exit code, stdout, and stderr

**Background Mode** (run_in_background=True):
  - Runs the command in a separate background process
  - Returns a bash_id (e.g., 'bash_1') for later monitoring
  - Use bash_output(bash_id) to retrieve output
  - Use kill_bash(bash_id) to terminate the process
  - Multiple background processes can run concurrently

**Output Format** (XML):
  All commands return XML-formatted output:
  - <status>: running, completed, timeout, error
  - <exit_code>: command exit code (if completed)
  - <stdout>: standard output
  - <stderr>: standard error
  - <bash_id>: background process ID (if run_in_background=True)

Usage:
  - The command argument is required
  - Optional timeout in milliseconds (max 90000ms, default 90000ms)
  - Use run_in_background=True only for commands that keep running until manually stopped, such as dev servers, file watchers, or `tail -f`.
  - VERY IMPORTANT: Do not use bash for codebase search or file reading. Use the dedicated `grep`, `glob`, and `read` tools instead of commands like `find`, `grep`, `cat`, `head`, or `tail`.
  - VERY IMPORTANT: Before project-specific commands, confirm the correct project directory and environment. The shell starts in the agent working directory, so do not assume the target repo or virtual environment is already active.
  - VERY IMPORTANT: Always use absolute paths for file arguments, redirects, and artifacts. Avoid patterns like `cd <dir> && command relative/path`; prefer `command /absolute/path` directly.
  - VERY IMPORTANT: Commands that may prompt for input will hang. Use explicit non-interactive flags when available. Typical examples: `apt-get install -y ...`, `dnf install -y ...`, `pacman -S --noconfirm ...`, `npm init -y`, `npm install -D ...`.
  - VERY IMPORTANT: Installs, builds, tests, scaffolding, and migrations must stay in the foreground. Do not put short-lived commands in the background and poll them with `bash_output`; run them directly and wait for the exit code.
    <bad>
      # Install command in the background, and still interactive
      bash("apt-get install ripgrep", run_in_background=True)
    </bad>
    <good>
      # Install in the foreground with a non-interactive flag
      bash("apt-get install -y ripgrep")
    </good>
    <bad>
      # Scaffolding command that may prompt
      bash("npm init")
    </bad>
    <good>
      bash("npm init -y")
    </good>
    <bad>
      # Dependency installation should finish in the foreground
      bash("npm install -D typescript eslint", run_in_background=True)
    </bad>
    <good>
      bash("npm install -D typescript eslint")
    </good>
    <bad>
      # Dev server in the foreground will block the tool call
      bash("npm run dev")
    </bad>
    <good>
      bash("npm run dev", run_in_background=True)
    </good>
    <bad>
      # Watchers and servers should not block the foreground shell
      bash("python -m http.server 8000")
    </bad>
    <good>
      bash("python -m http.server 8000", run_in_background=True)
    </good>
    <bad>
      cd /path/to/project && pytest tests/test_api.py
    </bad>
    <good>
      bash("pytest /path/to/project/tests/test_api.py")
    </good>
""".strip()

BASH_OUTPUT_DESCRIPTION = """
Retrieves output from a running or completed background bash shell identified by bash_id.

Usage:
  - Always returns only new output since the last check
  - Returns XML format with status, stdout, stderr, and timestamp
  - Supports an optional regex pattern to show only matching lines
  - Use this tool when you need to monitor or check the output of a background shell
""".strip()

KILL_BASH_DESCRIPTION = """
Terminates a running background bash shell identified by bash_id.

Usage:
  - Returns XML format with status (killed/error), bash_id, command, and message
  - Use this tool when you need to terminate a long-running background shell
""".strip()


@register_to_toolkit
class BashExecutor:
    """Executor for bash commands using subprocess state plus a persistent shell.

    Foreground commands share one persistent shell session so ``cd`` and
    ``export`` state survive across calls. Long-running processes can also be
    launched as detached background jobs and polled later by ``bash_id``.
    """

    def __init__(
        self,
        work_dir: Optional[str] = None,
        bash_envs: Optional[dict[str, str]] = None,
    ):
        """
        Initialize BashExecutor.

        Args:
            work_dir: Working directory for command execution. Defaults to current directory.
            bash_envs: Optional environment variables injected into the persistent shell.
        """
        self.work_dir = work_dir or str(Path.cwd())
        self.bash_envs = _normalize_bash_envs(bash_envs)
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)

        # persistent session (foreground commands)
        self._persistent_shell: Optional[PersistentShellSession] = None
        self._shell_lock: asyncio.Lock = asyncio.Lock()

        # background process management
        self._background_shells: dict[str, BackgroundShell] = {}
        self._next_shell_id: int = 1

    async def cleanup(self) -> None:
        """Cleanup BashExecutor, terminate all shells and processes."""
        # Tear down the foreground shell first so no new work is scheduled while cleanup runs.
        if self._persistent_shell and self._persistent_shell.is_active:
            try:
                await self._persistent_shell.stop()
            except Exception as e:
                logger.error(f"Error stopping persistent shell: {e}")

        # Then stop every background process and release its asyncio resources.
        for bash_id, bg_shell in list(self._background_shells.items()):
            try:
                bg_shell.process.terminate()
                await asyncio.wait_for(bg_shell.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                bg_shell.process.kill()
                await bg_shell.process.wait()
            except Exception as e:
                logger.error(f"Error killing {bash_id}: {e}")
            finally:
                await self._stop_background_shell(bg_shell)

        self._background_shells.clear()

    _MAX_OUTPUT_LINES = 600
    _MAX_BACKGROUND_BUFFER_CHARS = 200_000

    def _truncate_output(self, text: str) -> str:
        """Truncate output to _MAX_OUTPUT_LINES lines, keeping a head/tail window."""
        lines = text.splitlines()
        if len(lines) <= self._MAX_OUTPUT_LINES:
            return text
        head = self._MAX_OUTPUT_LINES // 2
        tail = self._MAX_OUTPUT_LINES - head
        omitted = len(lines) - self._MAX_OUTPUT_LINES
        truncated = lines[:head] + [f"\n... [{omitted} lines truncated] ...\n"] + lines[-tail:]
        return "\n".join(truncated)

    def _append_background_output(self, bg_shell: BackgroundShell, text: str) -> None:
        bg_shell.output_buffer += text
        overflow = len(bg_shell.output_buffer) - self._MAX_BACKGROUND_BUFFER_CHARS
        if overflow <= 0:
            return
        bg_shell.output_buffer = bg_shell.output_buffer[overflow:]
        bg_shell.read_position = max(0, bg_shell.read_position - overflow)

    def _close_process_transports(self, process: asyncio.subprocess.Process) -> None:
        """Close asyncio transports once a background process is fully done."""
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()

        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            stream_transport = getattr(stream, "_transport", None)
            if stream_transport is not None:
                stream_transport.close()

    def _validate_command(self, command: str) -> str | None:
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return None

        while tokens and ENV_ASSIGNMENT_PATTERN.match(tokens[0]):
            tokens.pop(0)
        if not tokens:
            return "Command cannot be empty."

        first = tokens[0]
        if INTERACTIVE_COMMAND_PATTERN.match(first):
            return (
                f"Refusing interactive command '{first}'. Use a non-interactive alternative "
                "or pass the required flags so the command can complete unattended."
            )
        if first in {"python", "python3", "node", "ipython", "bash", "sh", "zsh"} and len(tokens) == 1:
            return (
                f"Refusing interactive command '{first}'. Run a script, `-m` module, or "
                "other non-interactive command instead of starting a REPL."
            )
        if first == "cat" and len(tokens) == 1:
            return "Refusing interactive command 'cat'. Pass a file path or use the read tool instead."
        if first in SYSTEM_INSTALL_NONINTERACTIVE_FLAGS and "install" in tokens:
            required_flags = SYSTEM_INSTALL_NONINTERACTIVE_FLAGS[first]
            if not any(flag in tokens for flag in required_flags):
                required = ", ".join(sorted(required_flags))
                return (
                    f"Refusing potentially interactive install command '{first} install'. "
                    f"Re-run with an explicit non-interactive flag such as {required}."
                )
        if first == "pacman" and any(token.startswith("-S") for token in tokens[1:]):
            if "--noconfirm" not in tokens:
                return (
                    "Refusing potentially interactive install command 'pacman -S'. "
                    "Re-run with --noconfirm."
                )
        return None

    def _format_xml_output(
        self,
        status: str,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        bash_id: Optional[str] = None,
        command: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """Format output as XML"""
        parts = [f"<status>{status}</status>"]

        if exit_code is not None:
            parts.append(f"<exit_code>{exit_code}</exit_code>")

        if stdout:
            parts.append(f"<stdout>\n{html.escape(stdout)}\n</stdout>")

        if stderr:
            parts.append(f"<stderr>\n{html.escape(stderr)}\n</stderr>")

        if bash_id:
            parts.append(f"<bash_id>{html.escape(bash_id)}</bash_id>")

        if command:
            parts.append(f"<command>{html.escape(command)}</command>")

        if timestamp:
            parts.append(f"<timestamp>{html.escape(timestamp)}</timestamp>")

        return "\n".join(parts)

    async def _execute_in_persistent_shell(
        self, command: str, timeout: Optional[int]
    ) -> str:
        """Execute one foreground command inside the shared persistent shell."""
        # Serialize initialization and execution together so concurrent callers
        # cannot race and create multiple foreground shell sessions.
        async with self._shell_lock:
            # Lazily create the foreground shell only when the first command arrives.
            if self._persistent_shell is None or not self._persistent_shell.is_active:
                self._persistent_shell = PersistentShellSession(
                    work_dir=self.work_dir,
                    shell_type="bash",
                    env_vars=self.bash_envs,
                )
                await self._persistent_shell.start()
            try:
                timeout_seconds = 90 if timeout is None else min(timeout / 1000, 90)
                stdout, stderr, exit_code = await self._persistent_shell.execute(
                    command, timeout=timeout_seconds
                )

                # Normalize the response into the XML contract expected by the tool UI.
                stdout = self._truncate_output(stdout)
                stderr = self._truncate_output(stderr)

                return self._format_xml_output(
                    status="completed",
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                )

            except asyncio.TimeoutError:
                return self._format_xml_output(
                    status="timeout",
                    stdout=f"Command timed out after {timeout_seconds}s",
                )
            except RuntimeError as e:
                # Mark the shell as dead so the next command forces a clean restart.
                logger.error(f"Persistent shell crashed: {e}")
                self._persistent_shell = None
                return self._format_xml_output(
                    status="error",
                    stdout=f"Shell session crashed: {str(e)}. Will restart on next command.",
                )

    async def _start_background_shell(self, command: str) -> str:
        """Start a background shell process and return its initial XML status."""
        bash_id = f"bash_{self._next_shell_id}"
        self._next_shell_id += 1

        try:
            # Background shells are detached from the persistent foreground session.
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.work_dir,
                start_new_session=True,
            )

            bg_shell = BackgroundShell(
                bash_id=bash_id,
                command=command,
                process=process,
                output_buffer="",
                read_position=0,
                output_task=asyncio.create_task(self._collect_background_output(bash_id)),
            )

            self._background_shells[bash_id] = bg_shell

            return self._format_xml_output(
                bash_id=bash_id, command=command, status="running"
            )

        except Exception as e:
            return self._format_xml_output(
                status="error",
                stdout=f"Failed to start background shell: {str(e)}",
            )

    async def _collect_background_output(self, bash_id: str):
        """Continuously collect output from a background process."""
        bg_shell = self._background_shells.get(bash_id)
        if not bg_shell:
            return

        try:
            # Keep appending output until the child exits; bash_output() reads incrementally.
            while True:
                chunk = await bg_shell.process.stdout.read(4096)
                if not chunk:
                    break
                self._append_background_output(
                    bg_shell,
                    chunk.decode("utf-8", errors="replace"),
                )
            await bg_shell.process.wait()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error collecting output for {bash_id}: {e}")
            self._append_background_output(bg_shell, f"\n[Error: {str(e)}]")
            raise ValueError(f"Command execution failed: {str(e)}")

    async def _stop_background_shell(self, bg_shell: BackgroundShell) -> None:
        """Stop a background shell process and clean up its async resources."""
        try:
            bg_shell.process.terminate()
            await asyncio.wait_for(bg_shell.process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            bg_shell.process.kill()
            await bg_shell.process.wait()

        try:
            await asyncio.wait_for(bg_shell.output_task, timeout=1.0)
        except asyncio.TimeoutError:
            bg_shell.output_task.cancel()
            try:
                await bg_shell.output_task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

        self._close_process_transports(bg_shell.process)

    async def bash(
        self,
        command: Annotated[str, "The bash command to execute"],
        timeout: Annotated[
            Optional[int],
            "Optional timeout in milliseconds (max 90000ms).",
        ] = None,
        run_in_background: Annotated[
            bool,
            "Set to true to run this command in the background. Use bash_output() to read the output later.",
        ] = False,
    ) -> list:
        """Run a foreground command or spawn a long-lived background process."""
        validation_error = self._validate_command(command)
        if validation_error is not None:
            return build_result(
                self._format_xml_output(status="error", stdout=validation_error)
            )

        # Foreground is the default path; background is reserved for commands that never exit.
        if run_in_background:
            return build_result(await self._start_background_shell(command))
        return build_result(await self._execute_in_persistent_shell(command, timeout))

    async def bash_output(
        self,
        bash_id: Annotated[str, "ID of the background shell (e.g., 'bash_1')"],
        pattern: Annotated[
            Optional[str],
            "Optional regular expression to filter output lines. Only lines matching this regex will be included.",
        ] = None,
    ) -> list:
        """Return only the unread output slice for one background shell."""
        if bash_id not in self._background_shells:
            return build_result(
                self._format_xml_output(
                    status="error", stdout=f"No background shell found with ID: {bash_id}"
                )
            )

        bg_shell = self._background_shells[bash_id]

        new_output = bg_shell.output_buffer[bg_shell.read_position :]
        bg_shell.read_position = len(bg_shell.output_buffer)

        if pattern and new_output:
            try:
                regex = re.compile(pattern)
                lines = new_output.splitlines(keepends=True)
                filtered_lines = [line for line in lines if regex.search(line)]
                new_output = "".join(filtered_lines)
            except re.error as e:
                return build_result(
                    self._format_xml_output(status="error", stdout=f"Invalid regex: {e}")
                )

        new_output = self._truncate_output(new_output)

        if bg_shell.process.returncode is None:
            status = "running"
            exit_code = None
        elif bg_shell.process.returncode == 0:
            status = "completed"
            exit_code = 0
        else:
            status = "failed"
            exit_code = bg_shell.process.returncode

        return build_result(
            self._format_xml_output(
                status=status,
                exit_code=exit_code,
                stdout=new_output,
                timestamp=datetime.now().isoformat(),
            )
        )

    async def kill_bash(
        self,
        bash_id: Annotated[
            str,
            "ID of the background shell to kill (e.g., 'bash_1').",
        ],
    ) -> list:
        """Terminate one tracked background shell and release its resources."""
        if not bash_id:
            return build_result(
                self._format_xml_output(
                    status="error",
                    stdout="bash_id is required.",
                )
            )
        if bash_id not in self._background_shells:
            return build_result(
                self._format_xml_output(
                    status="error",
                    stdout=f"No background shell found with ID: {bash_id}",
                )
            )

        bg_shell = self._background_shells[bash_id]

        try:
            command = bg_shell.command
            await self._stop_background_shell(bg_shell)
            del self._background_shells[bash_id]

            return build_result(
                self._format_xml_output(
                    status="killed",
                    bash_id=bash_id,
                    command=command,
                    stdout=f"Successfully killed shell: {bash_id}",
                )
            )

        except Exception as e:
            return build_result(
                self._format_xml_output(
                    status="error",
                    stdout=f"Failed to kill shell: {str(e)}",
                )
            )

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        """Return FunctionTool instances for bash, bash_output, and kill_bash."""
        del context
        return [
            tool(self.bash, description=BASH_DESCRIPTION),
            tool(self.bash_output, description=BASH_OUTPUT_DESCRIPTION),
            tool(self.kill_bash, description=KILL_BASH_DESCRIPTION),
        ]
