"""Image viewing and analysis tools for local and remote inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Sequence

from agent_framework import Content, FunctionTool, Message, SupportsChatGetResponse, tool
from ..tool_loader import register_to_toolkit
from ..tool_support import (
    ToolContext,
    build_result,
    require_absolute_path,
    resolve_chat_client,
    resolve_media,
    write_markdown_report,
)

VIEW_IMAGE_DESCRIPTION = """
Reads one image from the local filesystem or an http/https URL and displays it for visual analysis.

Usage:
  - `file_path` can be an absolute local path or an http/https image URL
  - Supports common image formats such as PNG, JPG, JPEG, GIF, WEBP, and BMP
  - The image content is returned immediately for visual inspection
  - When you need multiple images, call this tool multiple times in the same batch
""".strip()

ANALYZE_IMAGE_DESCRIPTION = """
Analyzes one or more images and returns a structured Markdown assessment.

Usage:
  - `image_paths` must be a list of 1 to 4 absolute local image paths or http/https image URLs
  - `task` must mention every resolved input image reference exactly as it appears in the inputs
  - `output_path` is optional; when provided, the analysis is written to that absolute Markdown path
  - Keep `task` specific about what matters: usability, comparison baseline, visible regressions, document readability, or chart integrity

Example tasks:
  - Analyze `top.png` and explain whether it is a usable screen capture or a failure state.
  - Compare `reference-top.png` and `current-top.png`. Treat `reference-top.png` as the baseline and describe the most important visible differences in `current-top.png`.
  - Review `report-page.png` and explain whether the document is readable, whether any text is clipped, and whether the chart labels are legible.
""".strip()

ANALYZE_IMAGE_SYSTEM_PROMPT = """You are an image analysis assistant.

Analyze only what is visibly present in the provided images. Do not infer hidden state, off-screen content, implementation details, or prior/future screens unless the evidence is directly visible.

Input contract:
- The user provides a task that names every input image reference.
- Each image appears with a stable marker in the form `Image name: <source reference>`.
- The corresponding image data or image URL follows that marker.

Response style:
- Return concise, structured Markdown that directly answers the task.
- Use the provided source references exactly when referring to images.
- If multiple images are present, compare them only as requested.
- If the task names a baseline image, describe other images relative to that baseline.
- If something cannot be confirmed visually, say so explicitly.

Generic examples:
1. Single-image usability check
   Task: `Analyze page-top.png and explain whether it is a usable screenshot.`
   Focus: visible blockers, missing sections, rendering failures, legibility, and whether the capture is fit for review.

2. Multi-image comparison
   Task: `Compare reference-top.png and current-top.png. Treat reference-top.png as the baseline.`
   Focus: the most important visual differences first, especially layout shifts, missing content, broken assets, and changed text.

3. Document or chart QA
   Task: `Review report-page.png for readability and chart issues.`
   Focus: clipping, blur, overlap, truncated labels, unreadable legends, and other visible quality problems.
"""


@register_to_toolkit
@tool(description=VIEW_IMAGE_DESCRIPTION)
async def view_image(
    file_path: Annotated[
        str,
        "The absolute path to the image file to view, or an http/https image URL.",
    ],
) -> list:
    """Load one local or remote image and return it as a displayable tool result."""
    source, error = resolve_media(file_path, allowed_modalities={"image"})
    if error is not None:
        return build_result(error)
    assert source is not None

    try:
        data = source.data if source.data is not None else source.path.read_bytes()
    except OSError as exc:
        return build_result(str(exc))

    summary = f"Viewed image: {source.name}"
    result: list[Content] = build_result(summary, display_text=summary)
    result.append(Content.from_data(data, media_type=source.media_type))
    return result


@register_to_toolkit
class ImageAnalysisManager:
    """Provides the analyze_image tool using the current runtime chat client."""

    def __init__(
        self,
        chat_client: SupportsChatGetResponse | None = None,
        context: ToolContext | None = None,
    ):
        self._default_chat_client = chat_client
        self._context = context

    @property
    def chat_client(self) -> SupportsChatGetResponse | None:
        return resolve_chat_client(self._default_chat_client, self._context)

    async def analyze_image(
        self,
        image_paths: Annotated[
            Sequence[str],
            "A list of 1 to 4 absolute image paths or image URLs. The task must mention every source reference.",
        ],
        task: Annotated[
            str,
            "The analysis request. It must mention every input image reference and define the desired level of detail.",
        ],
        output_path: Annotated[
            str | None,
            "Optional absolute path to write a Markdown analysis record. If omitted, the tool returns the analysis directly.",
        ] = None,
    ) -> list[Content]:
        """Analyze one to four local or remote images with the active chat model."""
        chat_client = self.chat_client
        if chat_client is None:
            raise RuntimeError("analyze_image requires the current agent model, but no chat_client is available.")

        output_path_obj: Path | None = None
        if output_path is not None:
            output_path_obj, output_error = require_absolute_path(output_path, parameter_name="output_path")
            if output_error is not None:
                return build_result(output_error)

        if not image_paths:
            return build_result("image_paths must contain at least one source.")
        if len(image_paths) > 4:
            return build_result("image_paths supports at most 4 sources per call.")

        sources = []
        for image_path in image_paths:
            source, error = resolve_media(image_path, allowed_modalities={"image"})
            if error is not None:
                return build_result(error)
            assert source is not None
            if any(existing.name == source.name for existing in sources):
                return build_result(
                    "input sources must not contain duplicate references."
                )
            sources.append(source)

        image_names = [source.name for source in sources]
        missing = [name for name in image_names if name not in task]
        if len(image_names) > 1 and missing:
            return build_result(f"task must mention every input source reference. Missing: {str(missing)}")

        user_contents: list[Content] = []
        for source in sources:
            user_contents.append(Content.from_text(f"Image name: `{source.name}`"))
            try:
                data = source.data if source.data is not None else source.path.read_bytes()
            except OSError as exc:
                return build_result(str(exc))
            user_contents.append(Content.from_data(data, media_type=source.media_type))
        user_contents.append(Content.from_text(task))

        try:
            response = await chat_client.get_response(
                [
                    Message("system", [Content.from_text(ANALYZE_IMAGE_SYSTEM_PROMPT)]),
                    Message("user", user_contents),
                ]
            )
        except Exception as exc:
            raise RuntimeError(f"Image analysis failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Image analysis failed: the model returned an empty response.")

        if output_path_obj is not None:
            image_lines = "\n".join(f"- `{source.name}`: {source.ref}" for source in sources)
            record = (
                "# Image Analysis Record\n\n"
                "## Task\n\n"
                f"{task}\n\n"
                "## Images\n\n"
                f"{image_lines}\n\n"
                "## Result\n\n"
                f"{text}\n"
            )
            try:
                summary = write_markdown_report(
                    output_path_obj,
                    record,
                    report_label="Image analysis",
                    subject_names=image_names,
                )
            except OSError as exc:
                return build_result(f"Failed to write file: {exc}")
            return build_result(summary, display_text=f"Wrote file: {output_path_obj.name}")

        return build_result(text, display_text=f"Analyzed images: {str(image_names)}")

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        """Return ``analyze_image`` bound to the current runtime ``ToolContext``."""
        bound_manager = ImageAnalysisManager(
            chat_client=self._default_chat_client,
            context=context,
        )
        return [tool(bound_manager.analyze_image, name="analyze_image", description=ANALYZE_IMAGE_DESCRIPTION)]
