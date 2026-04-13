"""Nano-Codex compatibility shims for the vendored Agent Framework."""

from .history_compaction_runtime import NanoInMemoryHistoryProvider
from .openai_chat_completion_client import NanoOpenAIChatCompletionClient
from .tool_invocation import apply_tool_invocation_metadata_patch

__all__ = [
    "NanoInMemoryHistoryProvider",
    "NanoOpenAIChatCompletionClient",
    "apply_tool_invocation_metadata_patch",
]

__version__ = "1.0.0"
