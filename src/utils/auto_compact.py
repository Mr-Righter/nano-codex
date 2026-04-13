"""Compact configuration and implementation for Nano-Codex."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from uuid import uuid4

from agent_framework._compaction import (
    EXCLUDED_KEY,
    GROUP_ANNOTATION_KEY,
    SUMMARY_OF_GROUP_IDS_KEY,
    SUMMARY_OF_MESSAGE_IDS_KEY,
    _format_messages_for_summary,
    _group_messages_by_id,
    _group_start_indices,
    _included_group_ids,
    _ordered_group_ids_from_annotations,
    _set_group_summarized_by_summary_id,
    set_excluded,
)
from agent_framework import (
    ChatResponse,
    Content,
    Message,
    annotate_message_groups,
    apply_compaction,
)

from src.agent_framework_patch.history_compaction_runtime import (
    COMPACTION_SUMMARY_KEY,
    count_excluded_messages,
    is_compaction_summary_message,
)


SUMMARIZE_PROMPT = """
Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
  - Errors that you ran into and how you fixed them
  - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
6. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
7. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
8. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.
                       
Here's an example of how your output should be structured:

<example>
    <analysis>
    [Your thought process, ensuring all points are covered thoroughly and accurately]
    </analysis>

    <summary>
    1. Primary Request and Intent:
    [Detailed description]

    2. Key Technical Concepts:
    - [Concept 1]
    - [Concept 2]
    - [...]

    3. Files and Code Sections:
    - [File Name 1]
        - [Summary of why this file is important]
        - [Summary of the changes made to this file, if any]
        - [Important Code Snippet]
    - [File Name 2]
        - [Important Code Snippet]
    - [...]

    4. Errors and fixes:
        - [Detailed description of error 1]:
        - [How you fixed the error]
        - [User feedback on the error if any]
        - [...]

    5. Problem Solving:
    [Description of solved problems and ongoing troubleshooting]

    6. All user messages: 
        - [Detailed non tool use user message]
        - [...]

    7. Pending Tasks:
    - [Task 1]
    - [Task 2]
    - [...]

    8. Current Work:
    [Precise description of current work]

    9. Optional Next Step:
    [Optional Next step to take]

    </summary>
</example>
""".strip()

CONTINUATION_PROMPT = """
This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

<summary>
{summary}
</summary>

Please continue with the last task that you were asked to work on without asking the user any further questions.
""".strip()


@dataclass
class AutoCompactConfig:
    """Configuration for Nano-Codex auto-compaction."""

    max_tokens: int = 200000
    keep_last_groups: int = 0
    summarizer_model: Optional[str] = None
    summarize_prompt: str = SUMMARIZE_PROMPT

    def __post_init__(self) -> None:
        if self.max_tokens < 100:
            raise ValueError("max_tokens must be at least 100")
        if self.keep_last_groups < 0:
            raise ValueError("keep_last_groups must be at least 0")


@dataclass
class VisibleHistorySelection:
    """Visible history slice that will be summarized and replaced."""

    summarize_group_ids: list[str]
    messages_to_summarize: list[Message]
    insertion_index: int


@dataclass(frozen=True)
class CompactionOutcome:
    """Result of a compaction attempt, without UI side effects."""

    messages: list[Message]
    was_compacted: bool
    total_tokens: int
    max_tokens: int
    strategy: str
    remaining: int
    current_tokens: int = 0
    summary_text: str | None = None


def _visible_group_messages(
    grouped_messages: dict[str, list[Message]],
    group_id: str,
) -> list[Message]:
    """Return the non-excluded messages that still belong to one annotated group."""
    return [
        message
        for message in grouped_messages.get(group_id, [])
        if not message.additional_properties.get("_excluded", False)
    ]


def _preserved_system_group_ids(
    grouped_messages: dict[str, list[Message]],
    visible_group_ids: list[str],
) -> list[str]:
    """Keep leading system-only groups visible instead of summarizing them away."""
    preserved: list[str] = []
    for group_id in visible_group_ids:
        group_messages = _visible_group_messages(grouped_messages, group_id)
        if not group_messages:
            continue
        if any(is_compaction_summary_message(message) for message in group_messages):
            break
        if any(message.role != "system" for message in group_messages):
            break
        preserved.append(group_id)
    return preserved


def _select_visible_history(
    messages: list[Message],
    *,
    keep_last_groups: int,
) -> VisibleHistorySelection | None:
    """Choose the visible history slice that should be summarized next."""
    ordered_group_ids = _ordered_group_ids_from_annotations(messages)
    visible_group_ids = _included_group_ids(messages, ordered_group_ids)
    if not visible_group_ids:
        return None

    grouped_messages = _group_messages_by_id(messages)
    group_start_indices = _group_start_indices(messages)
    preserved_group_ids = _preserved_system_group_ids(grouped_messages, visible_group_ids)
    retained_candidates = [group_id for group_id in visible_group_ids if group_id not in preserved_group_ids]
    if not retained_candidates:
        return None

    tail_group_ids = retained_candidates[-keep_last_groups:] if keep_last_groups > 0 else []
    tail_group_id_set = set(tail_group_ids)
    summarize_group_ids = [group_id for group_id in retained_candidates if group_id not in tail_group_id_set]
    if not summarize_group_ids:
        return None

    messages_to_summarize: list[Message] = []
    for group_id in summarize_group_ids:
        messages_to_summarize.extend(_visible_group_messages(grouped_messages, group_id))
    if not messages_to_summarize:
        return None

    insertion_index = min(
        group_start_indices[group_id]
        for group_id in summarize_group_ids
        if group_id in group_start_indices
    )
    return VisibleHistorySelection(
        summarize_group_ids=summarize_group_ids,
        messages_to_summarize=messages_to_summarize,
        insertion_index=insertion_index,
    )


def _apply_continuation_summary(
    messages: list[Message],
    *,
    selection: VisibleHistorySelection,
    continuation_text: str,
) -> bool:
    """Insert the synthesized continuation summary and exclude the summarized messages."""
    if not continuation_text.strip():
        return False

    summary_id = f"nano_summary_{uuid4().hex}"
    original_message_ids = [message.message_id for message in selection.messages_to_summarize if message.message_id]
    summary_message = Message(
        "system",
        [Content.from_text(continuation_text)],
        message_id=summary_id,
        additional_properties={
            COMPACTION_SUMMARY_KEY: True,
            "compact_reason": "llm_summarize",
            GROUP_ANNOTATION_KEY: {
                SUMMARY_OF_MESSAGE_IDS_KEY: original_message_ids,
                SUMMARY_OF_GROUP_IDS_KEY: list(selection.summarize_group_ids),
            },
        },
    )

    changed = False
    for message in selection.messages_to_summarize:
        _set_group_summarized_by_summary_id(message, summary_id)
        changed = set_excluded(message, excluded=True, reason="llm_summarize") or changed

    messages.insert(selection.insertion_index, summary_message)
    annotate_message_groups(messages, from_index=selection.insertion_index, force_reannotate=False)
    return changed or True


def _included_message_count(messages: list[Message]) -> int:
    """Count the messages that remain visible after compaction."""
    return sum(1 for message in messages if not message.additional_properties.get(EXCLUDED_KEY, False))


def _latest_visible_summary_text(messages: list[Message]) -> str | None:
    """Return the newest visible synthetic continuation summary, if one exists."""
    for message in reversed(messages):
        if is_compaction_summary_message(message) and not message.additional_properties.get(EXCLUDED_KEY, False):
            return message.text
    return None


def _build_compaction_outcome(
    *,
    messages: list[Message],
    before_excluded: int,
    max_tokens: int,
    total_tokens: int | None,
    summary_usage: dict[str, int | None] | None,
    strategy: str = "llm_summarize",
) -> CompactionOutcome:
    """Build one normalized compaction outcome from the mutated full history."""
    summary_usage = summary_usage or {}
    summary_input_tokens = summary_usage.get("input_token_count")
    summary_output_tokens = summary_usage.get("output_token_count")
    before_tokens = summary_input_tokens if isinstance(summary_input_tokens, int) else (total_tokens or 0)
    after_tokens = summary_output_tokens if isinstance(summary_output_tokens, int) else 0
    was_compacted = count_excluded_messages(messages) > before_excluded
    return CompactionOutcome(
        messages=messages,
        was_compacted=was_compacted,
        total_tokens=before_tokens,
        max_tokens=max_tokens,
        strategy=strategy,
        remaining=_included_message_count(messages),
        current_tokens=after_tokens,
        summary_text=_latest_visible_summary_text(messages) if was_compacted else None,
    )


class SummaryCompactStrategy:
    """Summary projection strategy for Nano-Codex.

    The caller decides when to invoke the strategy. In automatic mode that
    trigger comes from the last observed ``usage_details.total_token_count``.
    """

    def __init__(
        self,
        *,
        max_tokens: int,
        summary_get_response: Callable[..., Awaitable[ChatResponse]],
        keep_last_groups: int,
        summarize_prompt: str,
        continuation_prompt: str = CONTINUATION_PROMPT,
        on_compacted: Callable[[CompactionOutcome], None] | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.summary_get_response = summary_get_response
        self.keep_last_groups = keep_last_groups
        self.summarize_prompt = summarize_prompt
        self.continuation_prompt = continuation_prompt
        self.on_compacted = on_compacted
        self.last_summary_usage: dict[str, int | None] | None = None
        self.last_trigger_total_tokens: int | None = None

    def set_trigger_total_tokens(self, total_tokens: int | None) -> None:
        """Record the token count that triggered automatic compaction."""
        self.last_trigger_total_tokens = total_tokens if isinstance(total_tokens, int) else None

    async def _summarize(self, messages_to_summarize: list[Message]) -> str | None:
        response = await self.summary_get_response(
            messages=[
                Message("system", [Content.from_text(self.summarize_prompt)]),
                Message("user", [Content.from_text(_format_messages_for_summary(messages_to_summarize))]),
            ],
            stream=False,
        )
        usage = response.usage_details if isinstance(response.usage_details, dict) else None
        self.last_summary_usage = usage
        summary_text = (response.text or "").strip()
        return summary_text or None

    async def __call__(self, messages: list[Message]) -> bool:
        selection = _select_visible_history(messages, keep_last_groups=self.keep_last_groups)
        if selection is None:
            return False

        before_excluded = count_excluded_messages(messages)
        try:
            summary_text = await self._summarize(selection.messages_to_summarize)
        except Exception:
            return False

        if not summary_text:
            return False

        if _apply_continuation_summary(
            messages,
            selection=selection,
            continuation_text=self.continuation_prompt.format(summary=summary_text),
        ):
            outcome = _build_compaction_outcome(
                messages=messages,
                before_excluded=before_excluded,
                max_tokens=self.max_tokens,
                total_tokens=self.last_trigger_total_tokens,
                summary_usage=self.last_summary_usage,
            )
            if outcome.was_compacted and self.on_compacted is not None:
                try:
                    self.on_compacted(outcome)
                except Exception:
                    pass
            return outcome.was_compacted
        return False


def make_compaction_strategy(
    config: AutoCompactConfig,
    get_response: Callable[..., Awaitable[ChatResponse]],
    *,
    on_compacted: Callable[[CompactionOutcome], None] | None = None,
) -> SummaryCompactStrategy:
    """Create the summary compaction strategy used by Nano-Codex."""
    return SummaryCompactStrategy(
        max_tokens=config.max_tokens,
        summary_get_response=get_response,
        keep_last_groups=config.keep_last_groups,
        summarize_prompt=config.summarize_prompt,
        on_compacted=on_compacted,
    )


def build_compaction_components(
    config: AutoCompactConfig | None,
    *,
    model: str,
    model_config_path: str | None,
    on_compacted: Callable[[CompactionOutcome], None] | None = None,
) -> tuple[SummaryCompactStrategy | None, None]:
    """Build the strategy/tokenizer pair passed into ``Agent(..., ...)``."""
    if config is None:
        return None, None

    from src.utils.model_client import create_chat_client

    summary_model = config.summarizer_model or model
    summary_client = create_chat_client(summary_model, config_path=model_config_path)
    return make_compaction_strategy(
        config,
        summary_client.get_response,
        on_compacted=on_compacted,
    ), None


async def compact_messages(
    config: AutoCompactConfig,
    messages: list[Message],
    get_response_func: Callable[..., Awaitable[ChatResponse]],
    *,
    total_tokens: int | None = None,
) -> CompactionOutcome:
    """Always attempt summary compaction against a concrete full-history list."""
    if not messages:
        return CompactionOutcome(
            messages=messages,
            was_compacted=False,
            total_tokens=total_tokens or 0,
            max_tokens=config.max_tokens,
            strategy="llm_summarize",
            remaining=0,
            current_tokens=0,
        )

    before_excluded = count_excluded_messages(messages)
    strategy = make_compaction_strategy(config, get_response_func)
    await apply_compaction(
        messages,
        strategy=strategy,
        tokenizer=None,
    )
    return _build_compaction_outcome(
        messages=messages,
        before_excluded=before_excluded,
        max_tokens=config.max_tokens,
        total_tokens=total_tokens,
        summary_usage=strategy.last_summary_usage,
    )


# Backward-compatible aliases for older imports.
_VisibleHistorySelection = VisibleHistorySelection
_SummaryCompactStrategy = SummaryCompactStrategy
_make_strategy = make_compaction_strategy


__all__ = [
    "AutoCompactConfig",
    "CompactionOutcome",
    "SUMMARIZE_PROMPT",
    "build_compaction_components",
    "compact_messages",
    "make_compaction_strategy",
    "SummaryCompactStrategy",
    "VisibleHistorySelection",
]
