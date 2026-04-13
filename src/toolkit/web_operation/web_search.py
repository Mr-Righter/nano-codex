"""Toolkit builder for web search and web fetch operations."""

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import re
import subprocess
import tempfile
import certifi
import requests
from typing import Annotated, Literal, Optional

from agent_framework import Content, FunctionTool, Message, SupportsChatGetResponse, tool
from markdownify import markdownify as md
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_TEXTUAL_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/xml",
}

from ..tool_loader import register_to_toolkit
from ..tool_support import ToolContext, build_result

# ==================== TOOL DESCRIPTIONS ====================

WEB_FETCH_DESCRIPTION = """
Fetches content from a specified URL and processes it using an AI model.

Usage:
  - Takes a URL and a prompt as input
  - Fetches the URL content, converts HTML to markdown
  - Processes the content with the prompt using a model
  - Returns the model's response about the content
  - Use this tool when you need to retrieve and analyze web content
  - When a URL redirects to a different host, you will be informed and should make a new request

Notes:
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
""".strip()

WEB_SEARCH_DESCRIPTION = """
Performs web searches and returns relevant information in Markdown format.

Usage:
  - Use this tool when you need to search for current information, facts, or resources on the web
  - The query parameter should be a clear and specific search query string
  - Returns formatted search results with titles, descriptions, and relevant details
  - Each result includes a title and detailed description or snippet
""".strip()

# ==================== PROMPTS ====================

WEB_FETCH_PROMPT = """Extract and analyze information from the web content below.

<url>{url}</url>

<content>
{content}
</content>

<task>{prompt}</task>

Provide a clear, well-structured response that directly addresses the task. Focus on relevant information and present it in an organized manner."""

LLM_SEARCH_PROMPT = """You are a helpful research assistant. Based on the following search query, provide relevant and accurate information.

<query>{query}</query>

Please provide <num_results>{num_results}</num_results> relevant pieces of information wrapped in <search_results> XML tags, using the following Markdown format:

<search_results>
### 1. [Topic/Title]

[Detailed explanation or description with relevant facts, examples, and context]

---

### 2. [Topic/Title]

[Detailed explanation or description with relevant facts, examples, and context]

---

### 3. [Topic/Title]

[Detailed explanation or description with relevant facts, examples, and context]
</search_results>

IMPORTANT:
- Use the EXACT format shown above with "### [number]. [Title]" on the first line
- Separate each result with "---" surrounded by blank lines
- Do NOT include URLs or date information
- Make sure the information is comprehensive, accurate, and directly related to the query
- Focus on providing useful knowledge and insights rather than just web search results
""".strip()


@dataclass(frozen=True)
class _FetchedPage:
    """Normalized page fetch result shared by the requests and curl paths."""

    final_url: str
    content_type: str | None
    body: str


def _normalize_content_type(content_type: str | None) -> str | None:
    """Return the MIME type without parameters so fetch checks stay stable."""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def _is_textual_content_type(content_type: str | None) -> bool:
    """Allow text-like documents and reject obvious binary payloads."""
    normalized = _normalize_content_type(content_type)
    return normalized is None or normalized.startswith("text/") or normalized in _TEXTUAL_CONTENT_TYPES


def _is_retryable_transport_error(error: requests.exceptions.RequestException) -> bool:
    """Retry requests TLS and transport failures with curl's system TLS stack."""
    return isinstance(
        error,
        (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    ) and not isinstance(error, requests.exceptions.HTTPError)


def _format_http_error(
    *,
    status_code: int | str | None,
    url: str,
    transport: str,
) -> str:
    """Return one consistent HTTP error message for requests and curl paths."""
    return f"Web fetch failed: HTTP {status_code or 'unknown'} while fetching {url} via {transport}."


# ==================== MAIN CLASS ====================


@register_to_toolkit
class WebSearchManager:
    """Expose read-only web search/fetch tools backed by Serper or an LLM.

    ``build_tools(context)`` rebinds the live ``ToolContext`` so subagents and
    model switches can reuse the current chat client without rebuilding the
    manager class itself.
    """

    def __init__(
        self,
        search_engine: Literal["serper", "llm"] = "serper",
        search_api_key: Optional[str] = None,
        chat_client: Optional[SupportsChatGetResponse] = None,
        search_num_results: int = 3,
        context: ToolContext | None = None,
    ):
        """Initialize WebSearchManager.

        Args:
            search_engine: Search engine to use ('serper' or 'llm')
            search_api_key: API key for search service (optional, can use SEARCH_API_KEY env var)
            chat_client: Chat client for LLM-based search (optional)
            search_num_results: Number of search results to return (default: 3)
        """
        self.search_engine = search_engine
        self.api_key = (
            search_api_key if search_api_key is not None else os.getenv("SEARCH_API_KEY", None)
        )
        self._default_chat_client = chat_client
        self.num_results = search_num_results
        self._context = context

    @property
    def chat_client(self) -> SupportsChatGetResponse | None:
        if self._context and self._context.chat_client is not None:
            return self._context.chat_client
        return self._default_chat_client

    async def web_search(
        self,
        query: Annotated[str, "The search query to use"],
    ) -> list:
        """Search the web and normalize the result into toolkit text content."""
        chat_client = self.chat_client
        if self.search_engine == "serper" and not self.api_key:
            raise ValueError("SEARCH_API_KEY environment variable not set for serper.")
        if self.search_engine == "llm" and chat_client is None:
            raise ValueError("chat_client is required for LLM-based search.")

        # Choose the backend first, then normalize both branches into one tool result shape.
        if self.search_engine == "serper":
            result_text = await self._search_with_serper(query)
        elif self.search_engine == "llm":
            result_text = await self._search_with_llm(query)
        else:
            raise ValueError(f"Unsupported search engine: {self.search_engine}")
        return build_result(result_text, display_text=f"Web search: {query}")

    async def _search_with_serper(self, query: str) -> str:
        """
        Perform Google search using Serper API.

        Args:
            query: Search query string.

        Returns:
            Search results in Markdown format.

        Raises:
            RuntimeError: If the API request fails.
        """
        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": query})
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

        try:
            response = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                data=payload,
                timeout=30,
            )
            response.raise_for_status()
            result_data = response.json()

            organic_results = result_data.get("organic", [])
            if not organic_results:
                return "No search results found."

            # Limit number of results
            organic_results = organic_results[: self.num_results]

            formatted_results = []
            for idx, item in enumerate(organic_results, 1):
                title = item.get("title", "No title")
                link = item.get("link", "")
                snippet = item.get("snippet", "No description available")
                date = item.get("date", "")

                result = f"### {idx}. {title}\n"
                result += f"**URL:** {link}\n\n"
                result += f"{snippet}\n"
                if date:
                    result += f"\n*Published: {date}*\n"
                formatted_results.append(result)

            return "\n---\n\n".join(formatted_results)

        except requests.exceptions.RequestException as e:
            logger.error(f"Serper API request failed: {e}")
            raise RuntimeError(f"Search failed: {str(e)}") from e

    async def _search_with_llm(self, query: str) -> str:
        """
        Perform intelligent search and summarization using LLM.

        Args:
            query: Search query string.

        Returns:
            Search results in Markdown format.

        Raises:
            RuntimeError: If LLM search fails or chat client is unavailable.
        """
        chat_client = self.chat_client
        if chat_client is None:
            logger.error("Chat client not provided, cannot perform LLM search")
            raise RuntimeError("LLM search failed: Chat client not available.")

        try:
            response = await chat_client.get_response(
                [
                    Message(
                        "user",
                        [Content.from_text(LLM_SEARCH_PROMPT.format(query=query, num_results=self.num_results))],
                    )
                ]
            )

            # ChatResponse.text provides the text content from the response
            if response.text:
                # Extract content from XML tags
                match = re.search(
                    r"<search_results>(.*?)</search_results>",
                    response.text,
                    re.DOTALL,
                )
                if match:
                    return match.group(1).strip()
                return response.text.strip()
            return "No search results found."

        except Exception as e:
            logger.error(f"LLM search failed: {str(e)}")
            raise RuntimeError(f"Search failed: {str(e)}") from e

    async def web_fetch(
        self,
        url: Annotated[str, "The URL to fetch content from"],
        prompt: Annotated[str, "The prompt to run on the fetched content"],
    ) -> list:
        """Fetch one URL, convert it to markdown, then ask the model to extract from it."""
        chat_client = self.chat_client
        if chat_client is None:
            logger.error("Chat client not provided, cannot perform web fetch")
            raise RuntimeError("Web fetch failed: Chat client not available.")

        try:
            # Phase 1: normalize the URL and download the raw page contents.
            if url.startswith("http://"):
                url = "https://" + url[7:]
            page = await self._fetch_page(url)

            original_domain = urlparse(url).netloc
            final_domain = urlparse(page.final_url).netloc

            # Cross-domain redirects are surfaced explicitly so the caller can decide trust.
            if original_domain != final_domain:
                result_text = (
                    f"REDIRECT DETECTED\n\nThe URL {url} redirected to a different domain: {page.final_url}\n\n"
                    "Please make a new WebFetch request with the redirect URL to fetch the content."
                )
                return build_result(result_text, display_text=f"Fetched URL: {url}")

            # Phase 2: convert the document to markdown before handing it to the model.
            if not _is_textual_content_type(page.content_type):
                content_type = _normalize_content_type(page.content_type) or "unknown"
                raise RuntimeError(
                    f"Web fetch failed: Unsupported content type '{content_type}' returned by {page.final_url}."
                )

            html_content = page.body
            markdown_content = md(
                html_content, heading_style="ATX", strip=["script", "style"]
            )
            if not markdown_content.strip():
                raise RuntimeError(
                    f"Web fetch failed: Empty textual content returned by {page.final_url}."
                )

            # Phase 3: ask the active model to extract only the task-relevant information.
            llm_response = await chat_client.get_response(
                [
                    Message(
                        "user",
                        [Content.from_text(WEB_FETCH_PROMPT.format(
                            url=url,
                            content=markdown_content[:100000],  # Limit content size
                            prompt=prompt,
                        ))],
                    )
                ]
            )

            if llm_response.text:
                result_text = llm_response.text.strip()
            else:
                result_text = "No content extracted from the web page."
            return build_result(result_text, display_text=f"Fetched URL: {url}")

        except RuntimeError:
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch URL {url}: {str(e)}")
            raise RuntimeError(f"Web fetch failed: {str(e)}") from e
        except Exception as e:
            logger.error(f"Web fetch failed for {url}: {str(e)}")
            raise RuntimeError(f"Web fetch failed: {str(e)}") from e

    async def _fetch_page(self, url: str) -> _FetchedPage:
        """Fetch a page with requests first, then curl for retryable TLS/transport failures."""
        try:
            return await asyncio.to_thread(self._fetch_with_requests, url)
        except requests.exceptions.RequestException as error:
            if isinstance(error, requests.exceptions.HTTPError):
                response = getattr(error, "response", None)
                raise RuntimeError(
                    _format_http_error(
                        status_code=getattr(response, "status_code", None),
                        url=getattr(response, "url", url),
                        transport="requests",
                    )
                ) from error
            if not _is_retryable_transport_error(error):
                raise RuntimeError(
                    f"Web fetch failed: Transport error while fetching {url}: {error}"
                ) from error

            try:
                return await asyncio.to_thread(self._fetch_with_curl, url)
            except RuntimeError as curl_error:
                curl_message = str(curl_error).removeprefix("Web fetch failed: ")
                if curl_message.startswith("HTTP "):
                    raise RuntimeError(
                        "Web fetch failed: Requests transport failed "
                        f"({error}); curl fallback returned {curl_message}."
                    ) from curl_error
                raise RuntimeError(
                    "Web fetch failed: TLS/transport failure while fetching "
                    f"{url}. Requests error: {error}; curl fallback error: {curl_message}"
                ) from curl_error

    def _fetch_with_requests(self, url: str) -> _FetchedPage:
        """Fetch a page with requests and normalize the successful response."""
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
            allow_redirects=True,
            verify=certifi.where(),
        )
        response.raise_for_status()

        return _FetchedPage(
            final_url=response.url,
            content_type=response.headers.get("Content-Type"),
            body=response.text,
        )

    def _fetch_with_curl(self, url: str) -> _FetchedPage:
        """Use system curl as a strict fallback when requests cannot establish transport."""
        try:
            with tempfile.TemporaryDirectory(prefix="nano-codex-web-fetch-") as temp_dir:
                body_path = os.path.join(temp_dir, "body.txt")
                result = subprocess.run(
                    [
                        "curl",
                        "--silent",
                        "--show-error",
                        "--location",
                        "--max-time",
                        "30",
                        "--connect-timeout",
                        "15",
                        "--user-agent",
                        "Mozilla/5.0",
                        "--output",
                        body_path,
                        "--write-out",
                        "%{http_code}\n%{content_type}\n%{url_effective}",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                meta_lines = result.stdout.splitlines()
                if len(meta_lines) < 3:
                    raise RuntimeError("curl did not return fetch metadata.")

                try:
                    status_code = int(meta_lines[0].strip())
                except ValueError as error:
                    raise RuntimeError(
                        f"curl returned an invalid status code: {meta_lines[0]!r}"
                    ) from error

                content_type = meta_lines[1].strip() or None
                final_url = meta_lines[2].strip() or url
                with open(body_path, "r", encoding="utf-8", errors="replace") as handle:
                    body = handle.read()
        except FileNotFoundError as error:
            raise RuntimeError("curl executable not found.") from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()
            raise RuntimeError(detail or "curl failed without an error message.") from error

        if status_code < 200 or status_code >= 300:
            raise RuntimeError(_format_http_error(status_code=status_code, url=final_url, transport="curl"))

        return _FetchedPage(
            final_url=final_url,
            content_type=content_type,
            body=body,
        )

    def build_tools(self, context: ToolContext) -> list[FunctionTool]:
        """Return ``web_search`` and ``web_fetch`` bound to the current runtime context."""
        bound_manager = WebSearchManager(
            search_engine=self.search_engine,
            search_api_key=self.api_key,
            chat_client=self._default_chat_client,
            search_num_results=self.num_results,
            context=context,
        )
        return [
            tool(bound_manager.web_search, name="web_search", description=WEB_SEARCH_DESCRIPTION),
            tool(bound_manager.web_fetch, name="web_fetch", description=WEB_FETCH_DESCRIPTION),
        ]
