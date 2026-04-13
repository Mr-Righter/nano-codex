"""Compact-aware session history runtime for Nano-Codex."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from agent_framework._compaction import EXCLUDED_KEY, apply_compaction, project_included_messages
from agent_framework._sessions import AgentSession, InMemoryHistoryProvider, SessionContext
from agent_framework._types import Message


PENDING_INPUTS_KEY = "_nano_pending_inputs"
LOOP_MANAGED_HISTORY_KEY = "_nano_loop_managed"
COMPACTION_SUMMARY_KEY = "is_compacted_msg"
LAST_TOTAL_TOKEN_COUNT_KEY = "_nano_last_total_token_count"


def get_history_provider_state(
    session: AgentSession,
    source_id: str = InMemoryHistoryProvider.DEFAULT_SOURCE_ID,
) -> dict[str, Any]:
    """Return the provider-owned session state bucket for one history source."""
    return session.state.setdefault(source_id, {})


def reset_history_runtime_state(state: dict[str, Any]) -> None:
    """Clear transient loop markers once a run is fully completed."""
    state.pop(PENDING_INPUTS_KEY, None)
    state.pop(LOOP_MANAGED_HISTORY_KEY, None)


def is_compaction_summary_message(message: Message) -> bool:
    """Return whether a message is a synthetic compacted continuation summary."""
    return bool(message.additional_properties.get(COMPACTION_SUMMARY_KEY))


def get_full_session_messages(
    session: AgentSession,
    source_id: str = InMemoryHistoryProvider.DEFAULT_SOURCE_ID,
) -> list[Message]:
    """Return the authoritative full history list stored in the session."""
    state = get_history_provider_state(session, source_id)
    messages = state.get("messages")
    if isinstance(messages, list):
        return messages
    state["messages"] = []
    return state["messages"]


def get_visible_session_messages(
    session: AgentSession,
    source_id: str = InMemoryHistoryProvider.DEFAULT_SOURCE_ID,
) -> list[Message]:
    """Return the included history view that should be visible to the LLM."""
    return project_included_messages(get_full_session_messages(session, source_id))


def get_last_total_token_count(
    session: AgentSession,
    source_id: str = InMemoryHistoryProvider.DEFAULT_SOURCE_ID,
) -> int | None:
    """Return the last observed model-call total token count for one session."""
    value = get_history_provider_state(session, source_id).get(LAST_TOTAL_TOKEN_COUNT_KEY)
    return value if isinstance(value, int) else None


class NanoInMemoryHistoryProvider(InMemoryHistoryProvider):
    """History provider that stores full history but loads only included messages."""

    def __init__(
        self,
        source_id: str | None = None,
        *,
        load_messages: bool = True,
        store_inputs: bool = True,
        store_context_messages: bool = False,
        store_context_from: set[str] | None = None,
        store_outputs: bool = True,
        skip_excluded: bool = True,
    ) -> None:
        super().__init__(
            source_id=source_id,
            load_messages=load_messages,
            store_inputs=store_inputs,
            store_context_messages=store_context_messages,
            store_context_from=store_context_from,
            store_outputs=store_outputs,
            skip_excluded=skip_excluded,
        )

    async def before_run(
        self,
        *,
        agent: Any,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        # Preserve the raw turn inputs so the loop runtime can merge them into
        # the authoritative full-history list before the first model call.
        state[PENDING_INPUTS_KEY] = list(context.input_messages)
        state[LOOP_MANAGED_HISTORY_KEY] = False
        history = await self.get_messages(context.session_id, state=state)
        context.extend_messages(self, history)

    async def after_run(
        self,
        *,
        agent: Any,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        handled = bool(state.get(LOOP_MANAGED_HISTORY_KEY, False))
        reset_history_runtime_state(state)
        if handled:
            # The custom function-invocation layer has already mutated the
            # authoritative full-history list in session state. Re-appending
            # inputs/outputs here would duplicate messages and undo compaction.
            return
        await super().after_run(agent=agent, session=session, context=context, state=state)


@dataclass
class LoopHistoryRuntime:
    """Runtime history state used by the custom function invocation layer."""

    full_history: list[Message]
    state: dict[str, Any] | None = None
    last_total_token_count: int | None = None

    @classmethod
    def from_inputs(
        cls,
        messages: Sequence[Message],
        *,
        session: AgentSession | None = None,
        source_id: str = InMemoryHistoryProvider.DEFAULT_SOURCE_ID,
    ) -> "LoopHistoryRuntime":
        """Build the per-run history runtime from session state plus new inputs.

        When ``session`` is present, this assumes ``NanoInMemoryHistoryProvider``
        has already captured the current turn inputs in ``PENDING_INPUTS_KEY``
        during ``before_run()``. The session-less branch is the only supported
        fallback for direct ``client.get_response(...)`` style calls.
        """
        if session is None:
            return cls(full_history=list(messages))

        state = get_history_provider_state(session, source_id)
        stored_messages = list(state.get("messages", []))
        pending_inputs_raw = state.pop(PENDING_INPUTS_KEY, None)
        pending_inputs = list(pending_inputs_raw) if isinstance(pending_inputs_raw, list) else []
        last_total_token_count = state.get(LAST_TOTAL_TOKEN_COUNT_KEY)

        if pending_inputs:
            stored_messages.extend(pending_inputs)

        state["messages"] = stored_messages
        state[LOOP_MANAGED_HISTORY_KEY] = True
        return cls(
            full_history=stored_messages,
            state=state,
            last_total_token_count=last_total_token_count if isinstance(last_total_token_count, int) else None,
        )

    def visible_history(self) -> list[Message]:
        return project_included_messages(self.full_history)

    async def prepare_messages(
        self,
        *,
        compaction_strategy: Any = None,
        tokenizer: Any = None,
    ) -> list[Message]:
        """Return the visible history projection after optional in-place compact.

        Automatic compaction is gated by the last observed response
        ``usage_details.total_token_count`` stored on this runtime.
        """
        if compaction_strategy is None:
            return self.visible_history()
        threshold = getattr(compaction_strategy, "max_tokens", None)
        if not isinstance(threshold, int):
            return self.visible_history()
        if self.last_total_token_count is None or self.last_total_token_count < threshold:
            return self.visible_history()
        set_trigger_total_tokens = getattr(compaction_strategy, "set_trigger_total_tokens", None)
        if callable(set_trigger_total_tokens):
            set_trigger_total_tokens(self.last_total_token_count)
        return await apply_compaction(
            self.full_history,
            strategy=compaction_strategy,
            tokenizer=None,
        )

    def append_messages(self, messages: Sequence[Message]) -> None:
        """Append newly generated messages to the authoritative full-history list."""
        self.full_history.extend(messages)

    def set_last_total_token_count(self, total_token_count: int | None) -> None:
        """Persist the latest single-call total token usage for future auto-compact checks."""
        self.last_total_token_count = total_token_count if isinstance(total_token_count, int) else None
        if self.state is None:
            return
        if self.last_total_token_count is None:
            self.state.pop(LAST_TOTAL_TOKEN_COUNT_KEY, None)
        else:
            self.state[LAST_TOTAL_TOKEN_COUNT_KEY] = self.last_total_token_count


def count_excluded_messages(messages: Sequence[Message]) -> int:
    """Return how many messages are currently excluded in a full-history list."""
    return sum(1 for message in messages if message.additional_properties.get(EXCLUDED_KEY, False))


__all__ = [
    "COMPACTION_SUMMARY_KEY",
    "LAST_TOTAL_TOKEN_COUNT_KEY",
    "LOOP_MANAGED_HISTORY_KEY",
    "NanoInMemoryHistoryProvider",
    "LoopHistoryRuntime",
    "PENDING_INPUTS_KEY",
    "count_excluded_messages",
    "get_full_session_messages",
    "get_history_provider_state",
    "get_last_total_token_count",
    "get_visible_session_messages",
    "is_compaction_summary_message",
    "reset_history_runtime_state",
]
