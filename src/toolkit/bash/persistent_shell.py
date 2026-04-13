"""Persistent shell implementation used by the bash toolkit."""

import asyncio
import logging
import re
import shlex
import time
from typing import Optional

logger = logging.getLogger(__name__)


ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PersistentShellSession:
    """Persistent shell session that preserves cwd and exported environment.

    Commands are wrapped with a delimiter protocol so Nano-Codex can reuse one
    shell process across calls while still recovering each command's stdout,
    stderr, and exit code separately.
    """

    def __init__(
        self,
        work_dir: str,
        shell_type: str = "bash",
        env_vars: Optional[dict[str, str]] = None,
    ):
        """
        Initialize persistent shell session.

        Args:
            work_dir: Working directory for the shell
            shell_type: Type of shell to use (default: bash)
            env_vars: Optional environment variables exported during shell startup.
        """
        self.work_dir = work_dir
        self.shell_type = shell_type
        self.env_vars = dict(env_vars or {})
        self.process: Optional[asyncio.subprocess.Process] = None
        self.command_counter = 0
        self.is_active = False

        # delimiter configuration
        self.DELIMITER = "<<<SHELL_OUTPUT_END>>>"
        self.COUNTER_VAR = "__SHELL_CMD_COUNTER"
        self.DELIMITER_VAR = "__SHELL_DELIMITER"

    async def start(self) -> None:
        """Start the shell process and initialize delimiter environment variables."""
        if self.is_active:
            return

        try:
            # Start a bare shell with minimal user config so output stays predictable.
            shell_args = [self.shell_type]
            if self.shell_type == "bash":
                shell_args.extend(["--norc", "--noprofile"])
            self.process = await asyncio.create_subprocess_exec(
                *shell_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.work_dir,
                start_new_session=True,
            )

            await self._initialize_shell()

            self.is_active = True

        except Exception as e:
            logger.error(f"Failed to start shell session: {e}")
            raise RuntimeError(f"Failed to start shell session: {str(e)}")

    async def _initialize_shell(self) -> None:
        """Initialize shell environment variables (delimiter, counter, cwd, env)."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Shell process not started")

        export_lines = []
        for key, value in self.env_vars.items():
            if not ENV_VAR_NAME_PATTERN.match(key):
                raise RuntimeError(f"Invalid shell environment variable name: {key!r}")
            export_lines.append(f"export {key}={shlex.quote(value)}")

        export_block = "\n".join(export_lines)
        if export_block:
            export_block += "\n"

        # Bootstrap the session once so later commands inherit cwd and env state.
        # CI=true and DEBIAN_FRONTEND=noninteractive reduce accidental prompts.
        init_commands = f"""
cd "{self.work_dir}"
export CI=true
export DEBIAN_FRONTEND=noninteractive
{export_block}export {self.DELIMITER_VAR}="{self.DELIMITER}"
export {self.COUNTER_VAR}=0
echo "INIT_COMPLETE"
"""
        self.process.stdin.write(init_commands.encode())
        await self.process.stdin.drain()

        # wait for initialization to complete
        try:
            while True:
                line = await asyncio.wait_for(
                    self.process.stdout.readline(), timeout=5.0
                )
                line_str = line.decode("utf-8", errors="replace").strip()
                if "INIT_COMPLETE" in line_str:
                    break
        except asyncio.TimeoutError:
            raise RuntimeError("Shell initialization timed out")

    async def execute(
        self, command: str, timeout: float = 120.0
    ) -> tuple[str, str, int]:
        """
        Execute a command and return (stdout, stderr, exit_code).

        Args:
            command: The command to execute.
            timeout: Timeout in seconds.

        Returns:
            (stdout, stderr, exit_code)

        Raises:
            RuntimeError: Session not started or no longer active.
            asyncio.TimeoutError: Command execution timed out.
        """
        if not self.is_active or not self.process:
            raise RuntimeError("Shell session is not active")

        if self.COUNTER_VAR in command or self.DELIMITER_VAR in command:
            raise ValueError(
                f"Command cannot contain shell session internal variables: "
                f"{self.COUNTER_VAR}, {self.DELIMITER_VAR}"
            )

        self.command_counter += 1
        expected_counter = self.command_counter

        # Wrap the command so the shell prints a machine-readable end marker with exit code.
        delimiter_printf = (
            f"printf '\\n%s:%s:%s\\n' "
            f'"${self.DELIMITER_VAR}" "${{{self.COUNTER_VAR}}}" "$__CMD_EXIT"'
        )
        wrapped_command = f"""{command}
__CMD_EXIT=$?
{self.COUNTER_VAR}=$(({self.COUNTER_VAR} + 1))
{delimiter_printf}
"""

        try:
            self.process.stdin.write(wrapped_command.encode())
            await self.process.stdin.drain()
        except Exception as e:
            self.is_active = False
            raise RuntimeError(f"Failed to send command: {str(e)}")

        # Read until the matching delimiter arrives or timeout recovery takes over.
        try:
            deadline = time.monotonic() + timeout
            stdout, stderr, exit_code = await self._read_until_delimiter(
                expected_counter, deadline
            )
            return stdout, stderr, exit_code
        except asyncio.TimeoutError:
            await self._recover_from_timeout(expected_counter)
            raise
        except Exception:
            self.is_active = False
            raise

    async def _drain_stream_until_idle(
        self,
        stream: asyncio.StreamReader,
        *,
        idle_timeout: float,
    ) -> bytes:
        chunks = bytearray()
        while True:
            try:
                chunk = await asyncio.wait_for(stream.read(4096), timeout=idle_timeout)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunk) < 4096:
                break
        return bytes(chunks)

    async def _has_child_processes(self, shell_pid: int) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-P", str(shell_pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            return proc.returncode == 0 and bool(stdout.strip())
        except Exception:
            return False

    async def _recover_from_timeout(self, expected_counter: int) -> None:
        """Kill stuck child processes and re-sync the shell after a command timeout.

        Attempts to preserve the bash session (and its pwd/env) by killing only
        the child processes and then letting the timed-out shell script finish and
        emit its own delimiter. If that fails, marks the session inactive so that
        BashExecutor recreates it on the next call.
        """
        if (
            not self.process
            or not self.process.stdin
            or self.process.returncode is not None
        ):
            self.is_active = False
            return

        # Phase 1: prefer killing only the timed-out child process tree.
        shell_pid = self.process.pid
        has_children = await self._has_child_processes(shell_pid)

        if has_children:
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "pkill", "-TERM", "-P", str(shell_pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
            except Exception:
                pass

        if not has_children:
            try:
                self.process.stdin.write(b"\n")
                await self.process.stdin.drain()
            except Exception:
                self.is_active = False
                return

        # Phase 2: try to re-sync by consuming the timed-out command's delimiter.
        try:
            await self._read_until_delimiter(expected_counter, time.monotonic() + 5.0)
            await self._drain_stream_until_idle(self.process.stdout, idle_timeout=0.05)
            await self._drain_stream_until_idle(self.process.stderr, idle_timeout=0.05)
            return
        except Exception:
            pass

        # Phase 3: escalate to SIGKILL, then mark the session dead if sync still fails.
        if has_children:
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "pkill", "-KILL", "-P", str(shell_pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
            except Exception:
                pass

        try:
            await self._drain_stream_until_idle(self.process.stdout, idle_timeout=0.1)
            await self._drain_stream_until_idle(self.process.stderr, idle_timeout=0.1)
        except Exception:
            pass

        self.is_active = False

    async def _read_until_delimiter(
        self, expected_counter: int, deadline: float
    ) -> tuple[str, str, int]:
        """
        Read output until the delimiter is encountered.

        Concurrently drains stderr to prevent the stderr pipe buffer from
        filling up and blocking the process.

        Args:
            expected_counter: The expected counter value in the delimiter.
            deadline: Absolute time (time.monotonic()) by which reading must finish.

        Returns:
            (stdout, stderr, exit_code)

        Raises:
            RuntimeError: Process terminated unexpectedly.
            asyncio.TimeoutError: Deadline exceeded before delimiter was found.
        """
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        delimiter_prefix = f"\n{self.DELIMITER}:{expected_counter}:".encode()

        async def drain_stderr() -> None:
            try:
                while True:
                    chunk = await self.process.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_buffer.extend(chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        stderr_task = asyncio.create_task(drain_stderr())

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError("Deadline exceeded waiting for delimiter")

                try:
                    # Read stdout in chunks while a side task continuously drains stderr.
                    chunk = await asyncio.wait_for(
                        self.process.stdout.read(4096), timeout=min(0.2, remaining)
                    )

                    if not chunk:
                        self.is_active = False
                        raise RuntimeError("Shell process terminated unexpectedly")

                    stdout_buffer.extend(chunk)
                    search_start = 0
                    while True:
                        prefix_index = stdout_buffer.find(delimiter_prefix, search_start)
                        if prefix_index == -1:
                            break

                        exit_start = prefix_index + len(delimiter_prefix)
                        exit_end = stdout_buffer.find(b"\n", exit_start)
                        if exit_end == -1:
                            break

                        exit_code_text = stdout_buffer[exit_start:exit_end].decode(
                            "utf-8", errors="replace"
                        )
                        if not exit_code_text.isdigit():
                            search_start = prefix_index + 1
                            continue

                        exit_code = int(exit_code_text)
                        stdout_text = stdout_buffer[:prefix_index].decode(
                            "utf-8", errors="replace"
                        ).rstrip("\n")
                        stderr_text = stderr_buffer.decode("utf-8", errors="replace").rstrip("\n")
                        return stdout_text, stderr_text, exit_code

                except asyncio.TimeoutError:
                    continue
        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        """Gracefully terminate the shell process."""
        if not self.process:
            return

        try:
            # Ask the shell to exit first so it can flush any remaining output cleanly.
            if self.process.stdin and not self.process.stdin.is_closing():
                self.process.stdin.write(b"exit\n")
                await self.process.stdin.drain()

            # Escalate only if the shell ignores a graceful exit.
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()

        except Exception as e:
            logger.error(f"Error stopping shell session: {e}")
        finally:
            self.is_active = False
