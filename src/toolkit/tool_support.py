"""Shared context and result helpers for toolkit tools."""

from __future__ import annotations

import dataclasses
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Optional, TypedDict
from urllib.parse import unquote, urlparse

import filetype
import requests
from agent_framework import Content
from src.ui.protocol import UiEventSink

if TYPE_CHECKING:
    from src.core.nano_codex import NanoCodexConfig


class ToolResultMetadata(TypedDict, total=False):
    """Metadata attached to tool result content items for UI middleware."""

    display_text: str
    source_name: str
    source_ref: str
    modality: str
    frame_index: int
    frame_count: int
    frame_time_seconds: float


MediaModality = Literal["image", "video"]


class MediaSource(NamedTuple):
    """Resolved image or video input — just data, no methods."""

    name: str
    ref: str
    media_type: str
    modality: MediaModality
    path: Path | None = None
    data: bytes | None = None


@dataclasses.dataclass
class ToolContext:
    """Runtime context shared across toolkit builders and subagents."""

    config: Any = dataclasses.field(default=None)
    chat_client: Any | None = None
    ui_sink: UiEventSink | None = None
    middleware: Any | None = None
    hidden_skills: list[str] | None = None
    dev_log_name: str = "dev_log.md"

    def __post_init__(self) -> None:
        if self.config is None:
            from src.core.nano_codex import NanoCodexConfig

            self.config = NanoCodexConfig()

    @property
    def work_dir(self) -> str:
        return self.config.work_dir

    @property
    def model_config_path(self) -> str:
        return self.config.model_config_path or "configs/model_config.json"

    @property
    def mcp_config_path(self) -> str:
        return self.config.mcp_config_path or "configs/mcp_config.json"

    @property
    def skills_dir(self) -> Optional[str]:
        return self.config.skills_dir

    @property
    def agents_dir(self) -> Optional[str]:
        return self.config.agents_dir

    @property
    def bash_envs(self) -> Optional[dict[str, str]]:
        return self.config.bash_envs

    @property
    def auto_compact_config(self) -> Any | None:
        return self.config.auto_compact_config

    @property
    def search_engine(self) -> str:
        return self.config.search_engine

    @property
    def search_api_key(self) -> Optional[str]:
        return self.config.search_api_key

    @property
    def search_num_results(self) -> int:
        return self.config.search_num_results

    @property
    def video_frame_fps(self) -> float:
        return self.config.video_frame_fps

    @property
    def video_max_frames(self) -> int:
        return self.config.video_max_frames


_REMOTE_MEDIA_HEADERS = {
    "User-Agent": "Nano-Codex/1.0 (+https://example.invalid)",
}


def build_result(
    text: str,
    *,
    display_text: str | None = None,
    data: bytes | None = None,
    media_type: str | None = None,
) -> list[Content]:
    """Build a tool result in the shape expected by function middleware."""

    if (data is None) != (media_type is None):
        raise ValueError("data and media_type must be provided together")

    text_metadata: ToolResultMetadata = {}
    if display_text is not None:
        text_metadata["display_text"] = display_text

    result = [Content.from_text(text, additional_properties=text_metadata or None)]
    if data is None:
        return result

    assert media_type is not None
    result.append(
        Content.from_data(
            data=data,
            media_type=media_type,
        )
    )
    return result


def extract_display_text(items: Sequence[Content]) -> str:
    """Extract the UI summary text from one tool result payload."""

    for item in items:
        metadata = item.additional_properties or {}
        display_text = metadata.get("display_text")
        if isinstance(display_text, str) and display_text:
            return display_text

    texts = [item.text for item in items if item.type == "text" and item.text]
    return "\n".join(texts)


def require_absolute_path(path_value: str, *, parameter_name: str) -> tuple[Path | None, str | None]:
    """Return a ``Path`` only when the provided argument is absolute."""

    path = Path(path_value)
    if not path.is_absolute():
        return None, f"{parameter_name} must be absolute: {path_value}"
    return path, None


def resolve_chat_client(default_client: Any | None, context: ToolContext | None) -> Any | None:
    """Prefer the runtime context chat client when one is currently bound."""

    if context is not None and context.chat_client is not None:
        return context.chat_client
    return default_client


def resolve_media(
    source: str,
    *,
    allowed_modalities: set[MediaModality] | None = None,
    parameter_name: str = "file_path",
) -> tuple[MediaSource | None, str | None]:
    """Resolve one local path or URL into a validated media source."""

    parsed = urlparse(source)
    is_remote = parsed.scheme in {"http", "https"}
    name = source if is_remote else Path(source).name

    path: Path | None = None
    data: bytes | None = None
    if is_remote:
        try:
            response = requests.get(source, headers=_REMOTE_MEDIA_HEADERS, timeout=30)
            response.raise_for_status()
            data = response.content
        except Exception as exc:
            return None, f"Failed to fetch URL: {source} ({exc})"
    else:
        path, error = require_absolute_path(source, parameter_name=parameter_name)
        if error is not None:
            return None, error
        assert path is not None
        if not path.exists():
            return None, f"File not found: {source}"
        if not path.is_file():
            return None, f"Path is not a regular file: {source}"

    sniffed = filetype.guess(data) if data is not None else filetype.guess(str(path))
    guessed_media_type = mimetypes.guess_type(name)[0]
    if is_remote and sniffed is None:
        return None, f"Unsupported media type for URL: {source} (could not detect a valid image/video payload)"

    media_type = sniffed.mime if sniffed is not None else guessed_media_type
    if media_type is None:
        media_type = "application/octet-stream"

    if media_type.startswith("image/"):
        modality: MediaModality = "image"
    elif media_type.startswith("video/"):
        modality = "video"
    else:
        location = source if is_remote else str(path)
        location_type = "URL" if is_remote else "file"
        return (
            None,
            f"Unsupported media type for {location_type}: {location} (detected type: {media_type})",
        )

    if allowed_modalities is not None and modality not in allowed_modalities:
        location = source if is_remote else str(path)
        location_type = "URL" if is_remote else "file"
        return (
            None,
            f"Unsupported media type for {location_type}: {location} (detected type: {media_type})",
        )

    return MediaSource(
        name=name,
        ref=source,
        media_type=media_type,
        modality=modality,
        path=path,
        data=data,
    ), None


def write_markdown_report(
    output_path: Path,
    content: str,
    *,
    report_label: str,
    subject_names: Sequence[str],
) -> str:
    """Persist one markdown report and return a summary line."""

    already_exists = output_path.exists()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    line_count = len(content.splitlines())

    subject_text = str(list(subject_names)) if subject_names else "the requested subject"

    message = (
        f"{report_label} for {subject_text} has been written to {output_path} "
        f"({line_count} lines)."
    )
    if already_exists:
        message += " Existing content was overwritten."
    message += " Use the `read` tool to inspect this analysis."
    return message


__all__ = [
    "MediaSource",
    "ToolContext",
    "ToolResultMetadata",
    "build_result",
    "extract_display_text",
    "require_absolute_path",
    "resolve_chat_client",
    "resolve_media",
    "write_markdown_report",
]
