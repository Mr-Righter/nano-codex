"""CLI entrypoint for launching Nano-Codex in interactive or single-task mode."""

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

from agent_framework import AgentSession, Content, Message
from src.core import NanoCodex, NanoCodexConfig
from src.middlewares import load_middlewares
from src.ui import SessionRestored, create_ui_runtime
from src.utils.auto_compact import AutoCompactConfig
from src.utils.history_io import load_session, save_session


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%m/%d/%Y %H:%M:%S",
    handlers=[logging.FileHandler("nano_codex_debug.log", mode="w")],
)
logger = logging.getLogger(__name__)


class TaskConfig(BaseModel):
    """Launcher-facing runtime configuration merged from YAML and CLI flags."""

    task: Optional[str] = Field(
        default=None,
        description="Task to run in single-task mode.",
    )
    is_interactive: bool = Field(
        default=True,
        description="Run in interactive chat mode when true.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Optional model alias that matches configs/model_config.json. When omitted, agent.md can supply the model.",
    )
    work_dir: str = Field(
        default="project",
        description="Working directory for agent execution.",
    )
    agent_loop_max_iterations: Optional[int] = Field(
        default=40,
        description="Optional maximum number of model round-trips in the function invocation loop.",
    )

    agent_config_path: Optional[str] = Field(
        default="agent.md",
        description="Path to the main agent markdown file.",
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
        description="Directory containing local skills.",
    )
    agents_dir: Optional[str] = Field(
        default="configs/agents",
        description="Directory containing subagent definitions.",
    )
    bash_envs: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional environment variables injected into the persistent shell session.",
    )

    middlewares: Optional[List[str]] = Field(
        default=None,
        description="Named middlewares to load.",
    )

    search_engine: str = Field(
        default="llm",
        description="Search backend for web_search ('serper' or 'llm').",
    )
    search_api_key: Optional[str] = Field(
        default=None,
        description="API key for the search backend.",
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

    auto_compact_enabled: bool = Field(
        default=False,
        description="Enable automatic compaction for long conversations.",
    )
    auto_compact_max_tokens: int = Field(
        default=200_000,
        description="Token budget before compaction triggers.",
    )
    auto_compact_keep_last_groups: int = Field(
        default=0,
        description="How many recent non-system message groups stay visible after automatic compaction.",
    )
    auto_compact_summarizer_model: Optional[str] = Field(
        default=None,
        description="Optional dedicated model for compaction summaries.",
    )

    history_file: Optional[str] = Field(
        default=None,
        description=(
            "Path to a session file to load. In interactive mode this also "
            "sets the auto-save path."
        ),
    )
    auto_save_history: bool = Field(
        default=True,
        description="Auto-save history to {work_dir}/.sessions/session_history.json.",
    )

    @classmethod
    def from_cli(cls):
        """Build configuration from ``nano_codex.yaml`` plus explicit CLI overrides."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--config",
            type=str,
            help="Path to YAML config file.",
            default="nano_codex.yaml",
        )

        for name, field_info in cls.model_fields.items():
            parser.add_argument(
                f"--{name}",
                help=field_info.description,
                default=argparse.SUPPRESS,
            )

        args = parser.parse_args()
        if args.config:
            logger.info("Loading configuration from: %s", args.config)
            config_dict = cls.load_config(args.config).model_dump()
        else:
            config_dict = {}

        cli_args = {k: v for k, v in vars(args).items() if k != "config"}
        config_dict.update(cli_args)
        return cls(**config_dict)

    @classmethod
    def load_config(cls, path: str):
        """Load one YAML config file into a validated ``TaskConfig``."""
        with open(path, "r", encoding="utf-8") as f:
            return cls(**yaml.safe_load(f))

    def save_config(self, path: str):
        """Persist the current launcher configuration to YAML."""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)


async def main():
    """Assemble the runtime and execute either the TUI or a single prompt run."""

    # Phase 1: load effective configuration and ensure the runtime workdir exists.
    config = TaskConfig.from_cli()
    logger.warning(
        "TaskConfig:\n%s",
        "\n".join(f"  {k}: {v}" for k, v in config.model_dump().items()),
    )
    os.makedirs(config.work_dir, exist_ok=True)

    # Phase 2: apply compatibility patches and derive runtime-only settings.
    from src.agent_framework_patch import apply_tool_invocation_metadata_patch

    apply_tool_invocation_metadata_patch()
    logger.info("Applied tool invocation metadata patch")

    auto_compact_config = (
        AutoCompactConfig(
            max_tokens=config.auto_compact_max_tokens,
            keep_last_groups=config.auto_compact_keep_last_groups,
            summarizer_model=config.auto_compact_summarizer_model,
        )
        if config.auto_compact_enabled
        else None
    )

    # Phase 3: translate launcher config into the agent/runtime objects.
    agent_config = NanoCodexConfig(
        **config.model_dump(include=set(NanoCodexConfig.model_fields.keys())),
        auto_compact_config=auto_compact_config,
    )

    middlewares = load_middlewares(config.middlewares)

    history_path: Optional[Path] = None
    if config.history_file:
        history_path = Path(config.history_file)
    elif config.auto_save_history:
        history_path = Path(config.work_dir) / ".sessions" / "session_history.json"

    # Phase 4: interactive mode delegates user IO to the Textual app/runtime pair.
    if config.is_interactive:
        from src.ui.tui.app import NanoCodexApp

        app = NanoCodexApp(history_file=history_path, config=config)
        ui_runtime = create_ui_runtime(
            "tui",
            app=app,
            window_size=app.TRANSCRIPT_WINDOW_SIZE,
        )
        agent = NanoCodex(
            config=agent_config,
            middleware=middlewares,
            ui_sink=ui_runtime.sink,
        )
        config.model = agent.config.model
        app.bind_runtime(agent=agent, ui_runtime=ui_runtime)
        await app.run_async()
        return

    # Phase 5: single-task mode restores session state, runs once, then persists it.
    ui_runtime = create_ui_runtime("console")
    agent = NanoCodex(
        config=agent_config,
        middleware=middlewares,
        ui_sink=ui_runtime.sink,
    )
    config.model = agent.config.model

    async with agent:
        if config.task is None:
            raise ValueError("Task must be provided when not in interactive mode.")

        session: Optional[AgentSession] = None
        if history_path and history_path.exists():
            try:
                session = load_session(history_path)
                ui_runtime.sink.emit(SessionRestored(path=history_path))
            except Exception as exc:
                logger.warning("Failed to load session: %s", exc)

        if session is None:
            session = agent.create_session()

        logger.info("Running single task: %s", config.task)
        await agent.run(
            Message("user", [Content.from_text(text=config.task)]),
            session=session,
        )

        if history_path:
            try:
                save_session(history_path, session)
            except Exception as exc:
                logger.warning("Failed to save session: %s", exc)


if __name__ == "__main__":
    asyncio.run(main())
