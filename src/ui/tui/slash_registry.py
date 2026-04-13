"""Slash command registry and built-in command implementations for the Nano-Codex TUI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from .slash_command import SlashCommand

if TYPE_CHECKING:
    from src.core.interactive_workflow import InteractiveWorkflow

logger = logging.getLogger(__name__)


@dataclass
class SlashCommandContext:
    """Runtime context passed to every slash command handler."""

    workflow: "InteractiveWorkflow"


SlashCommandFn = Callable[[SlashCommandContext], Awaitable[Optional[str]]]


class SlashCommandRegistry:
    """Registry mapping command strings to (SlashCommand, handler) pairs."""

    def __init__(self) -> None:
        self._cmds: dict[str, tuple[SlashCommand, SlashCommandFn]] = {}

    def register(self, cmd: SlashCommand, fn: SlashCommandFn) -> None:
        self._cmds[cmd.command] = (cmd, fn)

    def command(self, command: str, description: str) -> Callable[[SlashCommandFn], SlashCommandFn]:
        def decorator(fn: SlashCommandFn) -> SlashCommandFn:
            self.register(SlashCommand(command, description), fn)
            return fn

        return decorator

    def get(self, name: str) -> Optional[tuple[SlashCommand, SlashCommandFn]]:
        key = name if name.startswith("/") else f"/{name}"
        return self._cmds.get(key)

    def all(self) -> list[SlashCommand]:
        return sorted(
            [cmd for cmd, _ in self._cmds.values()],
            key=lambda c: c.command,
        )


REGISTRY = SlashCommandRegistry()


@REGISTRY.command("/exit", "Quit Nano-Codex")
async def _cmd_exit(ctx: SlashCommandContext) -> str:
    """Terminate the interactive workflow."""
    del ctx
    return "exit"


@REGISTRY.command("/compact", "Compress context via LLM summarisation")
async def _cmd_compact(ctx: SlashCommandContext) -> str:
    """Force one manual history compaction pass for the active session."""
    from src.utils.auto_compact import AutoCompactConfig, compact_messages
    from src.agent_framework_patch.history_compaction_runtime import (
        get_full_session_messages,
        get_last_total_token_count,
    )
    from src.utils.history_io import save_session
    from src.ui.compaction import emit_compaction_summary

    executor = ctx.workflow._agent_executor
    if executor is None:
        return "No active session to compact."

    messages = get_full_session_messages(executor._session)
    if not messages:
        return "No messages to compact."

    agent = ctx.workflow.agent
    base_cfg = agent.config.auto_compact_config

    force_config = AutoCompactConfig(
        max_tokens=base_cfg.max_tokens if base_cfg is not None else 100,
        keep_last_groups=0,
        **(
            {
                "summarize_prompt": base_cfg.summarize_prompt,
                "summarizer_model": base_cfg.summarizer_model,
            }
            if base_cfg is not None
            else {}
        ),
    )

    get_response = agent.client.get_response
    if force_config.summarizer_model:
        from src.utils.model_client import create_chat_client

        try:
            summarizer_client = create_chat_client(
                force_config.summarizer_model,
                config_path=agent.config.model_config_path,
            )
            get_response = summarizer_client.get_response
        except Exception:
            pass

    try:
        outcome = await compact_messages(
            force_config,
            messages,
            get_response,
            total_tokens=get_last_total_token_count(executor._session),
        )
    except Exception as exc:
        logger.error("/compact failed: %s", exc)
        return f"Compact failed: {exc}"

    if outcome.was_compacted:
        emit_compaction_summary(ctx.workflow.ui.sink, outcome)
    else:
        return "Compact skipped (nothing to compact)."

    if ctx.workflow.history_file:
        try:
            save_session(ctx.workflow.history_file, executor._session)
        except Exception as exc:
            logger.error("Failed to save session after compact: %s", exc)

    return "Context compacted."


@REGISTRY.command("/clear", "Clear conversation history (keep system messages)")
async def _cmd_clear(ctx: SlashCommandContext) -> str:
    """Remove non-system history from the active session and clear the transcript view."""
    from src.agent_framework_patch.history_compaction_runtime import (
        get_full_session_messages,
        get_history_provider_state,
        is_compaction_summary_message,
        reset_history_runtime_state,
    )
    from agent_framework import InMemoryHistoryProvider
    from src.utils.history_io import save_session

    executor = ctx.workflow._agent_executor
    if executor is None:
        return "No active session to clear."

    session = executor._session
    source_id = InMemoryHistoryProvider.DEFAULT_SOURCE_ID
    provider_state = get_history_provider_state(session, source_id)

    messages = get_full_session_messages(session, source_id)
    system_msgs = [
        m
        for m in messages
        if m.role == "system" and not is_compaction_summary_message(m)
    ]
    provider_state["messages"] = system_msgs
    reset_history_runtime_state(provider_state)

    if ctx.workflow.history_file:
        try:
            save_session(ctx.workflow.history_file, session)
        except Exception as exc:
            logger.error("Failed to save session after clear: %s", exc)

    controls = ctx.workflow.ui.controls
    if controls is None:
        return "Context cleared. Transcript clearing is unavailable in this mode."
    controls.clear_transcript_view()
    return f"Context cleared ({len(system_msgs)} system message(s) kept)."


@REGISTRY.command("/model", "Switch the active LLM model")
async def _cmd_model(ctx: SlashCommandContext) -> None:
    """Open the model picker for the current interactive session."""
    from src.utils.model_client import get_model_config_manager

    agent = ctx.workflow.agent
    try:
        mgr = get_model_config_manager(agent.config.model_config_path)
        models = tuple(mgr.list_models())
    except Exception as exc:
        return f"Failed to load model list: {exc}"

    current = agent.config.model
    controls = ctx.workflow.ui.controls
    if controls is None:
        return "Model picker is unavailable in this mode."
    controls.request_model_picker(models=models, current=current)
    return None


__all__ = [
    "REGISTRY",
    "SlashCommandContext",
    "SlashCommandFn",
    "SlashCommandRegistry",
]
