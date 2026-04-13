"""Video viewing and analysis tools for local and remote inputs."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

from agent_framework import Content, FunctionTool, Message, SupportsChatGetResponse, tool
from ..tool_loader import register_to_toolkit
from ..tool_support import (
    MediaSource,
    ToolContext,
    build_result,
    require_absolute_path,
    resolve_chat_client,
    resolve_media,
    write_markdown_report,
)

try:
    import cv2
except ImportError:  # pragma: no cover - dependency is optional at import time
    cv2 = None


_SAMPLE_FPS_MIN = 0.1
_SAMPLE_FPS_MAX = 10.0

VIEW_VIDEO_DESCRIPTION = """
Views one video input by sampling it into image frames for later inspection.

Usage:
  - `file_path` accepts one absolute local video path or one http/https video URL
  - The tool samples frames using the runtime video settings
  - The result is for visual inspection only; it does not analyze the video
  - Use this tool when the next step needs direct access to representative frames from a recording or screen capture
""".strip()

ANALYZE_VIDEO_DESCRIPTION = """
Analyzes one video input and returns a structured Markdown assessment.

Usage:
  - `video_path` accepts one absolute local video path or one http/https video URL
  - The video is sampled into image frames using the runtime video settings before analysis
  - `output_path` is optional; when provided, the analysis is written to that absolute Markdown path instead of returned inline
  - The task should describe what matters: recording quality, visible steps, issue reproduction, subtitles/captions, on-screen text, missing sections, loading failures, or stuck states
  - Subtitle/caption handling is limited to text visibly present in sampled frames; this tool does not transcribe audio

Example tasks:
  - Analyze this video and explain whether it is a usable screen recording or a failure state.
  - Summarize the major steps shown in this video and include approximate timestamps from the sampled frames.
  - Check whether this video clearly shows the reported bug or target event, and identify where it appears.
  - Read any visible subtitles, captions, or on-screen text in this video and summarize the key points.
""".strip()

ANALYZE_VIDEO_SYSTEM_PROMPT = """You are a video analysis assistant.

Analyze only what is visible in the sampled frames from the provided video. Do not infer audio content, hidden UI state, un-sampled moments, or implementation details unless they are directly supported by the sampled frames.

Input contract:
- The user provides a task describing what to inspect.
- The user message includes `Video name: <source reference>`.
- The remaining inputs are sampled frames labeled with the original video name and approximate timestamps.

Response style:
- Return concise, structured Markdown that directly answers the task.
- Refer to the original video name when describing evidence from frames.
- Treat timestamps as approximate because they come from sampled frames, not full playback.
- If subtitles, captions, or on-screen text are visible in frames, you may read and summarize that visible text.
- Do not claim to transcribe speech or audio; only describe text that is visibly present in the sampled frames.
- If a conclusion depends on footage that is not visible in the sampled frames, say that explicitly.
- Prefer scene order, key changes, visible anomalies, and actionable conclusions over generic commentary.

Generic examples:
1. Recording usability check
   Task: `Analyze this video and explain whether it is a usable screen recording or a failure state.`
   Focus: whether the recording shows enough stable, legible evidence to review and whether there are obvious blocking failures.

2. Step-by-step summary
   Task: `Summarize the major steps shown in this video and include approximate timestamps.`
   Focus: the visible sequence of scenes, major transitions, and when key steps appear in the sampled frames.

3. Issue reproduction review
   Task: `Check whether this video clearly shows the reported bug or target event, and identify where it appears.`
   Focus: where the issue first becomes visible, what immediately precedes it, and whether the captured frames are sufficient to confirm it.

4. Subtitle or on-screen text summary
   Task: `Read any visible subtitles, captions, or on-screen text in this video and summarize the key points.`
   Focus: only text visible in sampled frames, plus any obvious limits caused by sampling gaps or unreadable frames.
"""


def extract_video_frame_contents(
    source: MediaSource,
    *,
    fps: float,
    max_frames: int,
    with_text_annotations: bool = False,
) -> tuple[list[Content] | None, int, str | None]:
    """Extract sampled PNG frames for one local or remote video with OpenCV.

    Returns ``(contents, frame_count, error)``.  When *with_text_annotations*
    is True the result interleaves ``[text_label, image, text_label, image, ...]``;
    otherwise it contains only image Content items.
    """
    if source.modality != "video":
        return None, 0, f"Not a video source: {source.name}"
    if cv2 is None:
        return None, 0, "OpenCV is required for video inputs. Install opencv-python-headless."

    capture_path = str(source.path) if source.path is not None else None
    temp_path: str | None = None
    if capture_path is None:
        if source.data is None:
            return None, 0, f"Missing video data for source: {source.ref}"
        try:
            suffix = (
                Path(unquote(urlparse(source.ref).path)).suffix
                if source.path is None
                else Path(source.name).suffix
            ) or ".mp4"
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            ) as handle:
                handle.write(source.data)
                temp_path = handle.name
            capture_path = temp_path
        except OSError as exc:
            return None, 0, f"Failed to prepare video file: {source.ref} ({exc})"

    capture = cv2.VideoCapture(capture_path)
    if not capture.isOpened():
        capture.release()
        if temp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
        return None, 0, f"Failed to open video with OpenCV: {source.ref}"

    try:
        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frame_numbers = [0]
        if source_fps > 0 and total_frames > 0:
            sample_fps = min(max(float(fps), _SAMPLE_FPS_MIN), _SAMPLE_FPS_MAX)
            sample_limit = max(int(max_frames), 1)
            step = max(source_fps / sample_fps, 1.0)
            frame_numbers = []
            position = 0.0
            while int(position) < total_frames:
                frame_number = int(position)
                if not frame_numbers or frame_number != frame_numbers[-1]:
                    frame_numbers.append(frame_number)
                position += step
            if len(frame_numbers) > sample_limit:
                if sample_limit == 1:
                    frame_numbers = [frame_numbers[0]]
                else:
                    frame_numbers = sorted(
                        {
                            frame_numbers[round(index * (len(frame_numbers) - 1) / (sample_limit - 1))]
                            for index in range(sample_limit)
                        }
                    )

        extracted: list[tuple[float | None, bytes]] = []
        for frame_number in frame_numbers:
            if frame_number:
                capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_number))
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            ok, encoded = cv2.imencode(".png", frame)
            if not ok:
                continue
            extracted.append(
                (
                    (frame_number / source_fps) if source_fps > 0 else None,
                    encoded.tobytes(),
                )
            )
    finally:
        capture.release()
        if temp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)

    if not extracted:
        return None, 0, f"Failed to extract frames from video: {source.ref}"

    frame_count = len(extracted)
    contents: list[Content] = []
    for index, (timestamp, frame_bytes) in enumerate(extracted, start=1):
        if with_text_annotations:
            label = f"Video frame {index}/{frame_count} from `{source.name}`"
            if timestamp is not None:
                label += f" at {round(timestamp, 3)}s"
            contents.append(Content.from_text(label))
        contents.append(Content.from_data(frame_bytes, media_type="image/png"))

    return contents, frame_count, None


@register_to_toolkit
class VideoToolManager:
    """Provide dedicated view/analyze tools for video inputs."""

    def __init__(
        self,
        *,
        chat_client: SupportsChatGetResponse | None = None,
        context: ToolContext | None = None,
        video_frame_fps: float = 1.0,
        video_max_frames: int = 64,
    ) -> None:
        self._default_chat_client = chat_client
        self._context = context
        self.video_frame_fps = video_frame_fps
        self.video_max_frames = video_max_frames

    @property
    def chat_client(self) -> SupportsChatGetResponse | None:
        return resolve_chat_client(self._default_chat_client, self._context)

    @property
    def effective_video_frame_fps(self) -> float:
        return self._context.video_frame_fps if self._context is not None else self.video_frame_fps

    @property
    def effective_video_max_frames(self) -> int:
        return self._context.video_max_frames if self._context is not None else self.video_max_frames

    async def view_video(
        self,
        file_path: Annotated[
            str,
            "One absolute local video path or one http/https video URL.",
        ],
    ) -> list[Content]:
        """Load one video input as sampled frames for later inspection."""
        source, error = resolve_media(
            file_path,
            allowed_modalities={"video"},
            parameter_name="file_path",
        )
        if error is not None:
            return build_result(error)
        assert source is not None

        contents, frame_count, error = extract_video_frame_contents(
            source,
            fps=self.effective_video_frame_fps,
            max_frames=self.effective_video_max_frames,
            with_text_annotations=True,
        )
        if error is not None:
            return build_result(error)
        assert contents is not None

        summary = f"Viewed video: {source.name} ({frame_count} frames)"
        result = build_result(summary, display_text=summary)
        result.extend(contents)
        return result

    async def analyze_video(
        self,
        video_path: Annotated[
            str,
            "One absolute local video path or one http/https video URL.",
        ],
        task: Annotated[
            str,
            "The analysis request describing what to inspect in the sampled video frames.",
        ],
        output_path: Annotated[
            str | None,
            "Optional absolute path to write a Markdown analysis record. If omitted, the tool returns the analysis inline.",
        ] = None,
    ) -> list[Content]:
        """Analyze one local or remote video with the active chat model."""
        chat_client = self.chat_client
        if chat_client is None:
            raise RuntimeError("analyze_video requires the current agent model, but no chat_client is available.")

        output_path_obj = None
        if output_path is not None:
            output_path_obj, output_error = require_absolute_path(output_path, parameter_name="output_path")
            if output_error is not None:
                return build_result(output_error)

        source, error = resolve_media(
            video_path,
            allowed_modalities={"video"},
            parameter_name="video_path",
        )
        if error is not None:
            return build_result(error)
        assert source is not None

        contents, frame_count, error = extract_video_frame_contents(
            source,
            fps=self.effective_video_frame_fps,
            max_frames=self.effective_video_max_frames,
            with_text_annotations=True,
        )
        if error is not None:
            return build_result(error)
        assert contents is not None

        user_contents: list[Content] = [Content.from_text(f"Video name: `{source.name}`")]
        user_contents.extend(contents)
        user_contents.append(Content.from_text(task))

        try:
            response = await chat_client.get_response(
                [Message("system", [Content.from_text(ANALYZE_VIDEO_SYSTEM_PROMPT)]), Message("user", user_contents)]
            )
        except Exception as exc:
            raise RuntimeError(f"Video analysis failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Video analysis failed: the model returned an empty response.")

        if output_path_obj is None:
            return build_result(text, display_text=f"Analyzed video: `{source.name}`")

        record = (
            "# Video Analysis Record\n\n"
            "## Task\n\n"
            f"{task}\n\n"
            "## Video\n\n"
            f"- `{source.name}`: {source.ref}\n\n"
            "## Video Sampling\n\n"
            f"- fps: {self.effective_video_frame_fps}\n"
            f"- max_frames: {self.effective_video_max_frames}\n\n"
            "## Result\n\n"
            f"{text}\n"
        )
        try:
            summary = write_markdown_report(
                output_path_obj,
                record,
                report_label="Video analysis",
                subject_names=[source.name],
            )
        except OSError as exc:
            return build_result(f"Failed to write file: {exc}")
        return build_result(summary, display_text=f"Wrote file: {output_path_obj.name}")

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        """Return ``view_video`` and ``analyze_video`` bound to the runtime context."""
        bound = VideoToolManager(
            chat_client=self._default_chat_client,
            context=context,
            video_frame_fps=self.video_frame_fps,
            video_max_frames=self.video_max_frames,
        )
        return [
            tool(bound.view_video, name="view_video", description=VIEW_VIDEO_DESCRIPTION),
            tool(bound.analyze_video, name="analyze_video", description=ANALYZE_VIDEO_DESCRIPTION),
        ]
