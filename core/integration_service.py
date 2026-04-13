"""Integration service — detects third-party URLs in user queries,
checks connection status via the FSD Chat Service, and extracts content.

Providers supported: Google (Docs/Drive/Sheets), Notion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import httpx

from core.logger import get_logger
from core.settings import (
    CHAT_SERVICE_URL,
    INTEGRATION_POLL_INTERVAL_SEC,
    INTEGRATION_POLL_TIMEOUT_SEC,
)

logger = get_logger("IntegrationService")

Provider = Literal["google", "notion"]


@dataclass
class DetectedURL:
    provider: Provider
    url: str
    url_type: str  # "doc", "drive", "sheet", "notion_page"


@dataclass
class IntegrationContent:
    provider: Provider
    title: str
    content: str
    url: str
    metadata: dict = field(default_factory=dict)


_URL_PATTERNS: list[tuple[Provider, str, re.Pattern]] = [
    ("google", "doc",   re.compile(r"https?://docs\.google\.com/document/d/([\w-]+)", re.IGNORECASE)),
    ("google", "sheet", re.compile(r"https?://docs\.google\.com/spreadsheets/d/([\w-]+)", re.IGNORECASE)),
    ("google", "drive", re.compile(r"https?://drive\.google\.com/file/d/([\w-]+)", re.IGNORECASE)),
    ("notion", "notion_page", re.compile(r"https?://(?:www\.)?notion\.(so|site)/\S+", re.IGNORECASE)),
]


def detect_urls(text: str) -> list[DetectedURL]:
    """Find all integration URLs in a user message.

    Returns a list of DetectedURL, deduplicated by full URL.
    """
    seen: set[str] = set()
    results: list[DetectedURL] = []
    for provider, url_type, pattern in _URL_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(0).rstrip(".,;:!?\"')")
            if url not in seen:
                seen.add(url)
                results.append(DetectedURL(provider=provider, url=url, url_type=url_type))
    return results


class IntegrationClient:
    """Async HTTP client for the FSD Chat Service integration endpoints."""

    def __init__(self, base_url: str = CHAT_SERVICE_URL, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def check_all_status(self, token: str) -> dict[str, bool]:
        """Combined status check. Falls back to per-provider calls if combined fails."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/integration/status",
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") and isinstance(data.get("data"), dict):
                    return data["data"]
                raise ValueError("unexpected response shape")
        except Exception as exc:
            logger.warning("Combined status failed, falling back to per-provider",
                           error=str(exc))
            # Fallback: check each provider individually
            g = await self.check_provider_status("google", token)
            n = await self.check_provider_status("notion", token)
            return {
                "google": self.is_connected("google", g),
                "notion": self.is_connected("notion", n),
            }

    async def check_provider_status(self, provider: Provider, token: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/{provider}/status",
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("Failed to check provider status",
                         provider=provider, error=str(exc))
            return {"connected": False, "authenticated": False}

    def is_connected(self, provider: Provider, status_response: dict) -> bool:
        """Normalize the connected check. Google uses 'authenticated', Notion uses 'connected'."""
        if provider == "google":
            return status_response.get("authenticated", False)
        return status_response.get("connected", False)

    def get_user_display(self, provider: Provider, status_response: dict) -> str:
        if provider == "google":
            user = status_response.get("user", {})
            return user.get("email", user.get("name", "your Google account"))
        user = status_response.get("user", {})
        workspace = status_response.get("workspace", {})
        name = user.get("name", "") if user else ""
        ws_name = workspace.get("name", "") if workspace else ""
        return f"{name} ({ws_name})" if ws_name else (name or "your Notion account")

    async def get_auth_url(self, provider: Provider, token: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/{provider}",
                    headers=self._headers(token),
                    follow_redirects=False,
                )
                resp.raise_for_status()
                return resp.json().get("url")
        except Exception as exc:
            logger.error("Failed to get auth URL", provider=provider, error=str(exc))
            return None

    async def extract_content(
        self, provider: Provider, url: str, token: str
    ) -> IntegrationContent | None:
        """Extract content and normalize Google vs Notion response shapes."""
        body = {"url": url} if provider == "google" else {"input": url}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/{provider}/process",
                    headers={**self._headers(token), "Content-Type": "application/json"},
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.json() if exc.response else {}
            logger.error("Content extraction failed",
                         provider=provider, status=exc.response.status_code,
                         error=str(error_body))
            return None
        except Exception as exc:
            logger.error("Content extraction failed", provider=provider, error=str(exc))
            return None

        if not data.get("success", False):
            logger.warning("Extraction returned success=false",
                           provider=provider, data=str(data))
            return None

        if provider == "google":
            doc = data.get("document", {})
            return IntegrationContent(
                provider="google",
                title=doc.get("title", "Untitled Document"),
                content=doc.get("content", ""),
                url=url,
                metadata=doc.get("metadata", {}),
            )
        page = data.get("page", {})
        return IntegrationContent(
            provider="notion",
            title=page.get("title", "Untitled Page"),
            content=page.get("content", ""),
            url=url,
            metadata={"page_id": page.get("id", "")},
        )

    async def disconnect(self, provider: Provider, token: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/{provider}/disconnect",
                    headers=self._headers(token),
                )
                resp.raise_for_status()
                return resp.json().get("success", False)
        except Exception as exc:
            logger.error("Failed to disconnect", provider=provider, error=str(exc))
            return False


async def process_integration_urls(
    query: str,
    token: str,
    client: IntegrationClient | None = None,
) -> tuple[list[DetectedURL], dict[Provider, bool], list[IntegrationContent]]:
    """Full integration pipeline: detect URLs, check status, extract content."""
    detected = detect_urls(query)
    if not detected:
        return [], {}, []

    if client is None:
        client = IntegrationClient()

    providers = list({d.provider for d in detected})
    logger.info("Integration URLs detected",
                url_count=len(detected), providers=providers)

    all_status = await client.check_all_status(token)
    statuses: dict[Provider, bool] = {p: all_status.get(p, False) for p in providers}

    contents: list[IntegrationContent] = []
    for det in detected:
        if not statuses.get(det.provider, False):
            logger.info("Skipping extraction -- not connected", provider=det.provider)
            continue
        result = await client.extract_content(det.provider, det.url, token)
        if result:
            contents.append(result)
            logger.info("Content extracted",
                        provider=det.provider, title=result.title,
                        chars=len(result.content))
    return detected, statuses, contents
