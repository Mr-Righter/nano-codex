"""Textual TUI application for Nano-Codex interactive mode."""

from __future__ import annotations

import logging
import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from textual import containers, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import var
from textual.widgets import Footer, Label, Static, TextArea

from src.ui.protocol import UiRuntime

from .flash import Flash
from .widgets.model_select import ModelSelect, ModelSelectDismiss
from .widgets.slash_complete import Dismiss as SlashDismiss
from .widgets.slash_complete import SlashComplete
from .widgets.spinner_widget import SpinnerWidget
from .widgets.welcome_banner import WelcomeBanner

if TYPE_CHECKING:
    from .display import TextualDisplay


class Contents(containers.VerticalGroup):
    """Stream-layout container for transcript widgets."""


class PromptInput(TextArea):
    """TextArea with Enter-to-submit, Ctrl+J-for-newline, and slash-trigger."""

    BINDINGS = [
        Binding("enter", "submit_prompt", "Send", show=False, priority=True),
        Binding("ctrl+j", "insert_newline", "Newline", show=True),
        Binding("space", "insert_space", "Space", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class InvokeSlashComplete(Message):
        pass

    def action_submit_prompt(self) -> None:
        if text := self.text.strip():
            self.clear()
            self.post_message(PromptInput.Submitted(text))

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_insert_space(self) -> None:
        self.insert(" ")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.text == "/":
            self.post_message(PromptInput.InvokeSlashComplete())


class NanoCodexApp(App):
    """Textual TUI for interactive chat with a Nano-Codex runtime."""

    CSS_PATH = "app.tcss"
    TITLE = "Nano-Codex"
    DEFAULT_CSS = ""
    ENABLE_COMMAND_PALETTE = False
    DEFAULT_THEME = "textual-ansi"
    TRANSCRIPT_WINDOW_SIZE = 120

    BINDINGS = [
        Binding("ctrl+q", "exit_app", "Exit", show=True),
        Binding("ctrl+l", "clear_log", "Clear Screen", show=True),
    ]

    busy: var[bool] = var(False, toggle_class="-busy")
    show_slash_complete: var[bool] = var(False, toggle_class="-show-slash-complete")
    show_model_select: var[bool] = var(False, toggle_class="-show-model-select")

    def __init__(
        self,
        *,
        history_file: Optional[Path] = None,
        config=None,
    ) -> None:
        super().__init__()
        self._history_file = history_file
        self._config = config
        self._agent = None
        self._ui_runtime: UiRuntime | None = None
        self._tui_display: TextualDisplay | None = None

        self._input_ready = threading.Event()
        self._pending_input: str = ""
        self._quit_event = threading.Event()
        self._workflow = None

    def bind_runtime(self, *, agent, ui_runtime: UiRuntime) -> None:
        from .display import TextualDisplay

        if not isinstance(ui_runtime.sink, TextualDisplay):
            raise TypeError("NanoCodexApp requires a TextualDisplay sink.")
        self._agent = agent
        self._ui_runtime = ui_runtime
        self._tui_display = ui_runtime.sink

    def compose(self) -> ComposeResult:
        configured_model = getattr(self._config, "model", None) if self._config else None
        model = str(configured_model) if configured_model else "unknown"
        work_dir = str(
            Path(getattr(self._config, "work_dir", Path.cwd()) if self._config else Path.cwd()).expanduser().resolve()
        )

        with VerticalScroll(id="chat-window"):
            yield WelcomeBanner(model=model, work_dir=work_dir, id="welcome-banner")
            yield Contents(id="contents")
        with containers.Horizontal(id="status-bar"):
            yield SpinnerWidget(id="spinner")
            yield Label("", id="status-text")
            yield Label("", id="token-count")
        yield Flash(id="flash")
        yield SlashComplete(id="slash-complete")
        yield ModelSelect(id="model-select")
        with containers.VerticalGroup(id="input-area"):
            with containers.HorizontalGroup(id="input-row"):
                yield Label("❯", id="prompt-prefix")
                yield PromptInput(id="user-input", placeholder="Type / for commands")
        yield Footer()

    def on_mount(self) -> None:
        if self._agent is None or self._ui_runtime is None or self._tui_display is None:
            raise RuntimeError("NanoCodexApp.bind_runtime() must be called before run_async().")

        self.query_one("#spinner", SpinnerWidget).set_busy(False)

        from src.ui.tui.slash_registry import REGISTRY

        self.query_one("#slash-complete", SlashComplete).slash_commands = REGISTRY.all()
        self.query_one("#user-input", PromptInput).focus()

        self._tui_display.attach()
        self.set_interval(0.2, self._tui_display.poll_window)

        self.run_worker(self._run_workflow, exclusive=True, thread=True)

    @on(PromptInput.InvokeSlashComplete)
    def on_invoke_slash_complete(self) -> None:
        self.show_slash_complete = True
        self.query_one("#slash-complete", SlashComplete).focus()

    @on(SlashComplete.Completed)
    def on_slash_complete_completed(self, event: SlashComplete.Completed) -> None:
        self.show_slash_complete = False
        ta = self.query_one("#user-input", PromptInput)
        ta.clear()
        ta.insert(f"{event.command} ")
        ta.focus()

    @on(SlashDismiss)
    def on_slash_dismiss(self, event: SlashDismiss) -> None:
        if event.widget is self.query_one("#slash-complete", SlashComplete):
            self.show_slash_complete = False
            self.query_one("#user-input", PromptInput).focus()

    @on(ModelSelect.Completed)
    def on_model_select_completed(self, event: ModelSelect.Completed) -> None:
        self.show_model_select = False
        self._switch_model(event.model)
        self.query_one("#user-input", PromptInput).focus()

    @on(ModelSelectDismiss)
    def on_model_select_dismiss(self, event: ModelSelectDismiss) -> None:
        if event.widget is self.query_one("#model-select", ModelSelect):
            self.show_model_select = False
            self.query_one("#user-input", PromptInput).focus()

    def _show_model_picker(self, models: list[str], current: str | None) -> None:
        ms = self.query_one("#model-select", ModelSelect)
        self.show_slash_complete = False
        self.show_model_select = True
        ms.populate(models, current)
        self.call_after_refresh(self._focus_model_picker)

    def _focus_model_picker(self) -> None:
        if not self.show_model_select:
            return
        self.query_one("#model-select", ModelSelect).focus()

    def _switch_model(self, model: str) -> None:
        from src.utils.auto_compact import build_compaction_components
        from src.utils.model_client import create_chat_client
        from src.ui.compaction import build_compaction_ui_callback

        if self._workflow is None:
            self.flash_message("No active session to switch model.", style="warning")
            return

        agent = self._workflow.agent
        old_client = agent.client
        try:
            client_kwargs = {
                "config_path": agent.config.model_config_path,
                "instruction_role": getattr(old_client, "instruction_role", None),
                "function_invocation_configuration": getattr(
                    old_client,
                    "function_invocation_configuration",
                    None,
                ),
                "middleware": getattr(old_client, "middleware", None),
            }
            client_kwargs = {key: value for key, value in client_kwargs.items() if value is not None}
            new_client = create_chat_client(
                model,
                **client_kwargs,
            )
            compaction_strategy, tokenizer = build_compaction_components(
                agent.config.auto_compact_config,
                model=model,
                model_config_path=agent.config.model_config_path,
                on_compacted=build_compaction_ui_callback(self._ui_sink),
            )
            agent.client = new_client
            agent.compaction_strategy = compaction_strategy
            agent.tokenizer = tokenizer
            agent.config.model = model
            agent.default_options["model"] = getattr(new_client, "model", model)
            if hasattr(agent, "_tool_context"):
                agent._tool_context.chat_client = new_client
            update_agent_identity = getattr(agent, "_update_agent_name_and_description", None)
            if callable(update_agent_identity):
                update_agent_identity()
            if self._config is not None:
                self._config.model = model

            self.query_one("#welcome-banner", WelcomeBanner).query_one("#model", Static).update(
                WelcomeBanner.format_meta_line("model:", model)
            )
            self.flash_message(f"Model → {model}", style="success")
        except Exception as exc:
            self.flash_message(f"Failed to switch model: {exc}", style="warning")

    def _run_workflow(self) -> None:
        import asyncio

        asyncio.run(self._run_workflow_async())

    async def _run_workflow_async(self) -> None:
        from src.core.interactive_workflow import InteractiveWorkflow
        from src.ui.events import WarningNotice

        assert self._agent is not None
        assert self._ui_runtime is not None

        try:
            async with self._agent:
                wf = InteractiveWorkflow(
                    self._agent,
                    self._history_file,
                    ui=self._ui_runtime,
                )
                wf.build()
                self._workflow = wf
                await wf.drive(self._collect_workflow_responses)

        except Exception as exc:
            tb = traceback.format_exc()
            logging.getLogger(__name__).error("_run_workflow crashed:\n%s", tb)
            if self._ui_runtime is not None:
                self._ui_runtime.sink.emit(
                    WarningNotice(text=f"Fatal error: {exc}\n\nSee nano_codex_debug.log")
                )
        finally:
            self.call_from_thread(self.exit)

    async def _collect_workflow_responses(self, result) -> dict[str, str] | None:
        from src.core.interactive_workflow import InteractiveWorkflow

        if self._workflow is None:
            return None

        requests = InteractiveWorkflow.get_user_input_requests(result)
        if not requests:
            return None

        responses: dict[str, str] = {}
        for event in requests:
            self.call_from_thread(self._enable_input)
            self._input_ready.wait()
            self._input_ready.clear()

            if self._quit_event.is_set():
                return None

            responses[event.request_id] = self._pending_input

        return responses

    def _enable_input(self) -> None:
        self.set_status_ready()
        ta = self.query_one("#user-input", PromptInput)
        ta.disabled = False
        if not self.show_model_select and not self.show_slash_complete:
            ta.focus()

    def set_status_working(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.query_one("#spinner", SpinnerWidget).set_busy(True)
        self.query_one("#status-text", Label).update("")
        try:
            self.query_one("#prompt-prefix", Label).add_class("-busy-prefix")
        except Exception:
            pass

    def set_status_ready(self) -> None:
        if not self.busy:
            return
        self.busy = False
        self.query_one("#spinner", SpinnerWidget).set_busy(False)
        self.query_one("#status-text", Label).update("")
        try:
            self.query_one("#prompt-prefix", Label).remove_class("-busy-prefix")
        except Exception:
            pass

    def update_token_count(self, total: int) -> None:
        self.query_one("#token-count", Label).update(f"{total:,} tokens")

    def flash_message(self, content: str, *, style: str = "info", duration: float = 3.0) -> None:
        self.query_one("#flash", Flash).flash(content, style=style, duration=duration)

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        if self.show_slash_complete:
            self.show_slash_complete = False

        ta = self.query_one("#user-input", PromptInput)
        ta.disabled = True
        self.set_status_working()

        self._pending_input = event.text
        self._input_ready.set()

    def action_clear_log(self) -> None:
        if self._ui_runtime is not None and self._ui_runtime.controls is not None:
            self._ui_runtime.controls.clear_transcript_view()

    def action_exit_app(self) -> None:
        self._quit_event.set()
        self._input_ready.set()
        self.exit()

    def action_quit(self) -> None:
        self.action_exit_app()
