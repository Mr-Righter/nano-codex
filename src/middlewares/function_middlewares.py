"""Function-stage middleware for tool presentation and follow-up reminders."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from agent_framework import Content, FunctionInvocationContext, FunctionMiddleware
from src.ui.presenters import ToolResultPresenter
from src.ui.protocol import NullUiEventSink, UiEventSink

from .middleware_registry import register_middleware


@register_middleware("logging_function_result")
class ToolUiMiddleware(FunctionMiddleware):
    """Emit structured tool lifecycle events through the shared UI sink."""

    def __init__(
        self,
        ui_sink: UiEventSink | None = None,
        presenter: ToolResultPresenter | None = None,
    ) -> None:
        self._ui_sink = ui_sink or NullUiEventSink()
        self._presenter = presenter or ToolResultPresenter()

    def clone(self, ui_sink: UiEventSink | None = None) -> "ToolUiMiddleware":
        return type(self)(ui_sink=ui_sink or self._ui_sink, presenter=self._presenter)

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        state = self._presenter.capture_state(context)

        for event in self._presenter.start_events(state):
            self._ui_sink.emit(event)

        try:
            await call_next()
        except Exception:
            for event in self._presenter.failure_events(state):
                self._ui_sink.emit(event)
            raise

        for event in self._presenter.finish_events(state, context.result):
            self._ui_sink.emit(event)


def _format_system_reminder(*sentences: str) -> str:
    """Wrap reminder text in the synthetic tag format used by prompt middleware."""
    body = " ".join(sentence.strip() for sentence in sentences if sentence.strip())
    return f"\n\n<system-reminder>\n{body}\n</system-reminder>"


ReminderBuilder = Callable[[FunctionInvocationContext], str]


@register_middleware("tool_result_reminder")
class ToolResultReminderMiddleware(FunctionMiddleware):
    """Append short follow-up reminders to selected tool results.

    This keeps tool-specific operating rules close to the tool output that
    triggered them without hard-coding those rules into the tool bodies.
    """

    def __init__(
        self,
        reminder_builders: Mapping[str, ReminderBuilder] | None = None,
    ) -> None:
        self._reminder_builders = dict(reminder_builders or self.default_reminder_builders())

    def clone(self) -> "ToolResultReminderMiddleware":
        return type(self)(self._reminder_builders)

    @classmethod
    def default_reminder_builders(cls) -> dict[str, ReminderBuilder]:
        """Return the built-in tool-name -> reminder mapping."""
        return {
            "write_todos": cls._build_write_todos_reminder,
            "use_skill": cls._build_use_skill_reminder,
            "web_search": cls._build_web_search_reminder,
        }

    @staticmethod
    def _build_write_todos_reminder(context: FunctionInvocationContext) -> str:
        del context
        return _format_system_reminder(
            "Do NOT mention this reminder to the user.",
            "Keep todo statuses current while you work.",
            "`write_todos` overwrites the full list.",
        )

    @staticmethod
    def _build_use_skill_reminder(context: FunctionInvocationContext) -> str:
        del context
        return _format_system_reminder(
            "Do NOT mention this reminder to the user.",
            "If the skill points to other files, read the task-relevant ones now.",
            "Resolve relative paths against the skill's <location> value.",
        )

    @staticmethod
    def _build_web_search_reminder(context: FunctionInvocationContext) -> str:
        del context
        return _format_system_reminder(
            "Do NOT mention this reminder to the user.",
            "If you need the full content of a search result, call `web_fetch` with the URL and extraction goal.",
        )

    def _build_reminder(self, context: FunctionInvocationContext) -> str | None:
        tool_name = context.function.name
        builder = self._reminder_builders.get(tool_name)
        return builder(context) if builder is not None else None

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        # Execute the tool first so reminders are only attached to successful outputs.
        await call_next()

        # Append a synthetic reminder text item only for the selected tool names.
        reminder_text = self._build_reminder(context)
        if reminder_text:
            context.result.append(Content.from_text(reminder_text))


__all__ = ["ReminderBuilder", "ToolResultReminderMiddleware", "ToolUiMiddleware"]
