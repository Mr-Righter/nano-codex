"""Core agent assembly for Nano-Codex."""

from __future__ import annotations

from collections import defaultdict
from pprint import pprint
from typing import Any, Optional, Sequence

from agent_framework import (
    Agent,
    AgentRunInputs,
    AgentSession,
    ContextProvider,
    FunctionInvocationConfiguration,
    HistoryProvider,
    InMemoryHistoryProvider,
    MiddlewareTypes,
)
from pydantic import BaseModel, Field

from ..agent_framework_patch import NanoInMemoryHistoryProvider
from ..middlewares.middleware_registry import configure_middlewares
from ..toolkit import ToolContext, load_tools
from ..ui.compaction import build_compaction_ui_callback
from ..ui.protocol import UiEventSink
from ..utils.auto_compact import AutoCompactConfig, build_compaction_components
from ..utils.markdown_parser import AgentDefinition, parse_agent_definition_file
from ..utils.model_client import create_chat_client
from ..utils.prompt_assembler import SystemPromptAssembler


class NanoCodexConfig(BaseModel):
    """Configuration for the Nano-Codex runtime."""

    model: Optional[str] = Field(
        default=None,
        description="The name of the model to use, it should match an entry in model_config.json.",
    )
    work_dir: str = Field(default="project", description="The working directory for the task.")
    agent_loop_max_iterations: Optional[int] = Field(
        default=40,
        description="Optional maximum number of model round-trips in the function invocation loop.",
    )
    agent_config_path: Optional[str] = Field(
        default="agent.md",
        description="Path to the agent configuration file.",
    )
    model_config_path: Optional[str] = Field(
        default="configs/model_config.json",
        description="Path to the model configuration file.",
    )
    mcp_config_path: Optional[str] = Field(
        default="configs/mcp_config.json",
        description="Path to the MCP configuration file.",
    )
    skills_dir: Optional[str] = Field(
        default="configs/skills",
        description="Directory containing skill definitions.",
    )
    agents_dir: Optional[str] = Field(
        default="configs/agents",
        description="Directory containing subagent definitions.",
    )
    bash_envs: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional environment variables injected into the persistent shell session.",
    )
    search_engine: str = Field(
        default="llm",
        description="Search engine to use for web searches ('serper' or 'llm').",
    )
    search_api_key: Optional[str] = Field(
        default=None,
        description="API key for search service (e.g., Serper). If None, uses SEARCH_API_KEY env var.",
    )
    search_num_results: int = Field(
        default=3,
        description="Number of search results to return.",
    )
    video_frame_fps: float = Field(
        default=1.0,
        description="Sampling rate used when video inputs are converted into image frames.",
    )
    video_max_frames: int = Field(
        default=64,
        description="Maximum number of frames to extract from a single video input.",
    )
    auto_compact_config: Optional[AutoCompactConfig] = Field(
        default=None,
        description="Auto-compact configuration. When set, enables message history compaction.",
    )


class NanoCodex(Agent):
    """Configurable general-purpose agent scaffold built on ``agent_framework``."""

    def __init__(
        self,
        config: NanoCodexConfig,
        *,
        definition: AgentDefinition | None = None,
        id: Optional[str] = None,
        default_options: Optional[dict[str, Any]] = None,
        context_providers: Sequence[ContextProvider] | None = None,
        middleware: Sequence[MiddlewareTypes] | None = None,
        ui_sink: UiEventSink | None = None,
        **kwargs: Any,
    ):
        self.config = config
        self.work_dir = config.work_dir
        self.agent_definition = definition or parse_agent_definition_file(config.agent_config_path)
        compaction_callback = build_compaction_ui_callback(ui_sink)

        prompt_assembler = SystemPromptAssembler(
            base_instructions=self.agent_definition.instructions,
            skills_dir=config.skills_dir,
        )
        agent_instructions = prompt_assembler.assemble(
            work_dir=config.work_dir,
            skill_names=self.agent_definition.skills,
        )

        model = config.model or self.agent_definition.model
        if not model:
            raise ValueError(
                "Model name must be specified in either the agent config file or NanoCodexConfig."
            )
        self.config.model = model

        chat_client = create_chat_client(
            model,
            config_path=config.model_config_path,
            function_invocation_configuration=FunctionInvocationConfiguration(
                include_detailed_errors=True,
                max_iterations=config.agent_loop_max_iterations,
            ),
        )

        merged_default_options: dict[str, Any] = {}
        if self.agent_definition.default_options:
            merged_default_options.update(self.agent_definition.default_options)
        if default_options:
            merged_default_options.update(dict(default_options))

        configured_middleware = configure_middlewares(middleware, ui_sink=ui_sink)
        self._tool_context = ToolContext(
            config=config,
            chat_client=chat_client,
            ui_sink=ui_sink,
            middleware=list(configured_middleware) if configured_middleware else None,
            hidden_skills=self.agent_definition.hidden_skills,
        )

        all_tools = load_tools(
            self._tool_context,
            tool_names=self.agent_definition.tools,
            mcp_config_path=config.mcp_config_path,
            mcp_services=self.agent_definition.mcp_service,
        )

        compaction_strategy, tokenizer = build_compaction_components(
            config.auto_compact_config,
            model=model,
            model_config_path=config.model_config_path,
            on_compacted=compaction_callback,
        )
        configured_context_providers = self._ensure_history_provider(context_providers)

        super().__init__(
            chat_client,
            agent_instructions,
            id=id,
            name=self.agent_definition.name,
            description=self.agent_definition.description,
            tools=all_tools,
            default_options=merged_default_options,
            context_providers=configured_context_providers,
            middleware=configured_middleware,
            compaction_strategy=compaction_strategy,
            tokenizer=tokenizer,
            **kwargs,
        )

    def _ensure_history_provider(
        self,
        context_providers: Sequence[ContextProvider] | None,
    ) -> list[ContextProvider]:
        providers = list(context_providers or [])
        normalized: list[ContextProvider] = []
        has_default_history = False

        for provider in providers:
            if (
                isinstance(provider, InMemoryHistoryProvider)
                and not isinstance(provider, NanoInMemoryHistoryProvider)
                and provider.source_id == InMemoryHistoryProvider.DEFAULT_SOURCE_ID
            ):
                normalized.append(
                    NanoInMemoryHistoryProvider(
                        source_id=provider.source_id,
                        load_messages=provider.load_messages,
                        store_inputs=provider.store_inputs,
                        store_context_messages=provider.store_context_messages,
                        store_context_from=provider.store_context_from,
                        store_outputs=provider.store_outputs,
                    )
                )
                has_default_history = True
                continue

            normalized.append(provider)
            if (
                isinstance(provider, HistoryProvider)
                and provider.source_id == InMemoryHistoryProvider.DEFAULT_SOURCE_ID
            ):
                has_default_history = True

        if not has_default_history:
            normalized.insert(0, NanoInMemoryHistoryProvider())
        return normalized

    def run(
        self,
        messages: AgentRunInputs | None = None,
        *,
        session: AgentSession | None = None,
        tools: Any = None,
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        return super().run(
            messages,
            session=session,
            tools=tools,
            options=options,
            **kwargs,
        )

    def run_stream(
        self,
        messages: AgentRunInputs | None = None,
        *,
        session: AgentSession | None = None,
        tools: Any = None,
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        return super().run(
            messages,
            stream=True,
            session=session,
            tools=tools,
            options=options,
            **kwargs,
        )

