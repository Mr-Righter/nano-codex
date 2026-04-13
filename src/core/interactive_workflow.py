"""Interactive workflow orchestration between the human user and Nano-Codex."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Final, Optional

from agent_framework import (
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    AgentSession,
    Content,
    Executor,
    InMemoryCheckpointStorage,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowRunResult,
    handler,
    response_handler,
)
from rich.console import Console

from src.ui.events import (
    InfoNotice,
    SessionEnded,
    SessionRestored,
    SessionSaved,
    SessionStarted,
    UserMessageEvent,
    WarningNotice,
)
from src.ui.protocol import NULL_UI_RUNTIME, UiRuntime
from src.utils.history_io import load_session, save_session

from .nano_codex import NanoCodex

if TYPE_CHECKING:
    from src.ui.tui.slash_registry import SlashCommandRegistry


_console = Console()

USER_PROMPT: Final[str] = "You"
USER_PROXY_ID: Final[str] = "user_proxy"
EXIT_OUTPUT: Final[str] = "exit"
UNKNOWN_COMMAND_HINT: Final[str] = "type / in the input for available commands"


@dataclass(slots=True)
class UserInputRequest:
    """Request for user input from command line."""

    prompt: str = USER_PROMPT


class UserProxy(Executor):
    """Workflow executor that turns UI input into agent requests.

    ``UserProxy`` is the human-facing edge of the workflow graph. It requests
    the next line of input, handles slash commands locally, forwards normal
    messages to the ``AgentExecutor``, and re-arms input collection after each
    assistant response.
    """

    def __init__(
        self,
        id: str | None = None,
        registry: Optional["SlashCommandRegistry"] = None,
        workflow: Optional["InteractiveWorkflow"] = None,
    ) -> None:
        super().__init__(id=id or USER_PROXY_ID)
        self._registry = registry
        self._workflow = workflow

    async def _request_next_input(
        self,
        ctx: WorkflowContext[AgentExecutorRequest] | WorkflowContext,
    ) -> None:
        await ctx.request_info(
            request_data=UserInputRequest(),
            response_type=str,
        )

    async def _emit_exit(
        self,
        ctx: WorkflowContext[AgentExecutorRequest, str],
    ) -> None:
        await ctx.yield_output(EXIT_OUTPUT)

    def _warn(self, text: str) -> None:
        if self._workflow is not None:
            self._workflow.emit(WarningNotice(text=text))

    def _info(self, text: str) -> None:
        if self._workflow is not None:
            self._workflow.emit(InfoNotice(text=text))

    async def _handle_slash_command(
        self,
        message_text: str,
        ctx: WorkflowContext[AgentExecutorRequest, str],
    ) -> bool:
        if not message_text.startswith("/"):
            return False

        entry = self._registry.get(message_text) if self._registry else None
        if entry is None:
            self._warn(f"Unknown command: {message_text} ({UNKNOWN_COMMAND_HINT})")
            await self._request_next_input(ctx)
            return True

        if self._workflow is None:
            self._warn("Slash commands are unavailable before the workflow is initialized.")
            await self._request_next_input(ctx)
            return True

        from src.ui.tui.slash_registry import SlashCommandContext

        _, fn = entry
        slash_ctx = SlashCommandContext(workflow=self._workflow)
        try:
            result = await fn(slash_ctx)
        except Exception as exc:
            self._warn(f"Command error: {exc}")
            result = None

        if result == EXIT_OUTPUT:
            await self._emit_exit(ctx)
            return True

        if result:
            self._info(result)

        await self._request_next_input(ctx)
        return True

    @handler
    async def start(self, _: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        await self._request_next_input(ctx)

    @response_handler
    async def on_user_input(
        self,
        _original_request: UserInputRequest,
        user_message: str,
        ctx: WorkflowContext,
    ) -> None:
        message_text = user_message.strip()

        if not message_text:
            await self._request_next_input(ctx)
            return

        if await self._handle_slash_command(message_text, ctx):
            return

        if self._workflow is not None:
            self._workflow.emit(UserMessageEvent(text=message_text))

        user_msg = Message("user", [Content.from_text(text=message_text)])
        await ctx.send_message(
            AgentExecutorRequest(messages=[user_msg], should_respond=True)
        )

    @handler
    async def on_agent_response(
        self,
        _result: AgentExecutorResponse,
        ctx: WorkflowContext,
    ) -> None:
        await self._request_next_input(ctx)


class InteractiveWorkflow:
    """Interactive workflow connecting the user and a ``NanoCodex`` instance."""

    def __init__(
        self,
        agent: NanoCodex,
        history_file: Optional[Path] = None,
        *,
        ui: UiRuntime | None = None,
    ) -> None:
        self.agent = agent
        self.history_file = history_file
        self.ui = ui or NULL_UI_RUNTIME
        self._agent_executor: Optional[AgentExecutor] = None
        self._session_restored = False
        self._session_started = False
        self._session_closed = False
        self.workflow = None
        self.user_proxy: UserProxy = UserProxy(USER_PROXY_ID)

    def emit(self, event) -> None:
        """Forward one UI event to the active runtime sink."""
        self.ui.sink.emit(event)

    def _restore_session(self) -> AgentSession | None:
        """Load a persisted session when interactive history restore is enabled."""
        if self.history_file is None or not self.history_file.exists():
            return None

        try:
            session = load_session(self.history_file)
        except Exception as exc:
            self.emit(WarningNotice(text=f"Failed to load session: {exc}"))
            return None

        self._session_restored = True
        return session

    def _save_session(self) -> None:
        """Persist the current interactive session to disk when configured."""
        if self.history_file is None or self._agent_executor is None:
            return

        try:
            save_session(self.history_file, self._agent_executor._session)
            self.emit(SessionSaved(path=self.history_file))
        except Exception as exc:
            self.emit(WarningNotice(text=f"Failed to save session: {exc}"))

    def build(self) -> "InteractiveWorkflow":
        """Construct the user<->agent workflow graph and restore session state.

        The workflow is built lazily so callers can inject the agent and UI
        runtime first, then pay the setup cost only when interactive execution
        actually starts.
        """
        if self.workflow is not None and self._agent_executor is not None:
            return self

        from src.ui.tui.slash_registry import REGISTRY

        # Phase 1: rebuild the UI-aware user proxy and restore the last session.
        self.user_proxy = UserProxy(USER_PROXY_ID, registry=REGISTRY, workflow=self)
        restored_session = self._restore_session()

        # Phase 2: wire the bidirectional workflow between human input and agent output.
        self._agent_executor = AgentExecutor(self.agent, session=restored_session)
        self.workflow = (
            WorkflowBuilder(
                start_executor=self.user_proxy,
                checkpoint_storage=InMemoryCheckpointStorage(),
            )
            .add_edge(self.user_proxy, self._agent_executor)
            .add_edge(self._agent_executor, self.user_proxy)
        ).build()
        return self

    @staticmethod
    def get_user_input_requests(result: WorkflowRunResult) -> list:
        return [
            event
            for event in result.get_request_info_events()
            if isinstance(event.data, UserInputRequest)
        ]

    async def drive(
        self,
        response_provider: Callable[[WorkflowRunResult], Awaitable[dict[str, str] | None]],
    ) -> None:
        """Drive the interactive loop until the user exits or input stops.

        ``response_provider`` is UI-specific: console mode blocks on stdin,
        while the Textual app bridges requests to its input widget.
        """
        if self.workflow is None:
            self.build()
        assert self.workflow is not None

        # Phase 1: emit startup/session-restore events exactly once per lifecycle.
        if not self._session_started:
            self.emit(SessionStarted())
            if self._session_restored and self.history_file is not None:
                self.emit(SessionRestored(path=self.history_file))
            self._session_started = True

        try:
            # Phase 2: alternate between workflow outputs and user-provided responses.
            result = await self.workflow.run("start")
            while True:
                should_exit = any(
                    not hasattr(output, "messages") and str(output) == EXIT_OUTPUT
                    for output in result.get_outputs()
                )
                if should_exit:
                    break

                responses = await response_provider(result)
                if responses is None:
                    break

                result = await self.workflow.run(responses=responses)
        finally:
            # Phase 3: persist the last session snapshot and emit shutdown once.
            if not self._session_closed:
                self._save_session()
                if self._session_started:
                    self.emit(SessionEnded())
                self._session_closed = True

    async def run(self):
        """Run the interactive workflow using synchronous console input."""
        async def console_response_provider(result: WorkflowRunResult) -> dict[str, str] | None:
            requests = self.get_user_input_requests(result)
            if not requests:
                return None

            responses: dict[str, str] = {}
            for event in requests:
                self.emit(InfoNotice(text="─" * 40))
                responses[event.request_id] = _console.input(
                    f"[bold cyan]{event.data.prompt}[/bold cyan]: "
                )
            return responses

        await self.drive(console_response_provider)
