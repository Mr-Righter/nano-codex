"""Agent-stage middleware for last-minute message shaping before model execution."""

from typing import Awaitable, Callable

from agent_framework import AgentContext, Content, Message, agent_middleware
from .middleware_registry import register_middleware

# Keep the most drift-prone execution rules close to the latest user message.
_USER_MESSAGE_REMINDER = """<system-reminder>
Do NOT mention this reminder to the user.
- If the task is still in progress, do not send a text-only turn. Every in-progress turn must contain at least one tool call.
- If there is no todo list yet and the work is multi-step or non-trivial, consider using `write_todos` to track progress.
- If this task already has a todo list, keep it current. `write_todos` overwrites the entire list.
</system-reminder>"""


@register_middleware("user_message_reminder")
@agent_middleware
async def user_message_reminder_middleware(
    context: AgentContext, call_next: Callable[[], Awaitable[None]]
) -> None:
    """Inject a short reminder after the most recent user message."""
    messages = context.messages

    # Keep the reminder adjacent to the latest user intent so it survives long histories.
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            last_user_idx = i
            break

    if last_user_idx is not None:
        # The reminder is inserted as a synthetic user message because the framework
        # already preserves message ordering and role semantics through that path.
        reminder_msg = Message(
            "user",
            [Content.from_text(text=_USER_MESSAGE_REMINDER)],
        )
        context.messages = (
            list(messages[: last_user_idx + 1])
            + [reminder_msg]
            + list(messages[last_user_idx + 1 :])
        )

    await call_next()
