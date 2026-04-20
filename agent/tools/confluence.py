"""
Confluence REST API client.

Reads pages from a Confluence space using the Confluence REST API v1.
Credentials are loaded from environment variables / .env file.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML stripper (for cleaning Confluence storage-format HTML)
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: List[str] = []
        self._skip_tags = {"style", "script"}
        self._in_skip = False

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._in_skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._in_skip = False

    def handle_data(self, data):
        if not self._in_skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(p.strip() for p in self._parts if p.strip())


def _strip_html(html_content: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_content)
        return stripper.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_content)


# ---------------------------------------------------------------------------
# Page data model
# ---------------------------------------------------------------------------

class ConfluencePage:
    def __init__(self, page_id: str, title: str, body_text: str, space_key: str = ""):
        self.page_id = page_id
        self.title = title
        self.body_text = body_text   # plain-text content (HTML stripped)
        self.space_key = space_key

    def __repr__(self) -> str:
        snippet = self.body_text[:80].replace("\n", " ")
        return f"<ConfluencePage id={self.page_id} title={self.title!r} body={snippet!r}...>"


# ---------------------------------------------------------------------------
# Confluence REST API client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """
    Reads Confluence pages via the Confluence REST API v1.
    Set CONFLUENCE_BASE_URL, CONFLUENCE_USERNAME, and CONFLUENCE_API_TOKEN
    in your .env file.
    """

    def __init__(
        self,
        rest_base_url: str,
        rest_username: str,
        rest_api_token: str,
    ):
        if not rest_base_url:
            raise ValueError(
                "CONFLUENCE_BASE_URL is required. Set it in your .env file."
            )
        self._base_url = rest_base_url.rstrip("/")
        self._auth = (rest_username, rest_api_token)

    async def connect(self) -> None:
        """No-op - kept for interface compatibility."""
        pass

    async def disconnect(self) -> None:
        """No-op - kept for interface compatibility."""
        pass

    async def list_pages(self, space_key: str) -> List[Dict[str, Any]]:
        """Return a list of page metadata dicts for all pages in a space."""
        url = f"{self._base_url}/rest/api/content"
        params = {"spaceKey": space_key, "limit": 250, "expand": "version"}
        async with httpx.AsyncClient(auth=self._auth, timeout=30) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])

    async def get_page(self, page_id: str) -> Optional[ConfluencePage]:
        """Fetch a single page and return its plain-text body."""
        try:
            url = f"{self._base_url}/rest/api/content/{page_id}"
            params = {"expand": "body.storage,title"}
            async with httpx.AsyncClient(auth=self._auth, timeout=30) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            title = data.get("title", "")
            html_body = data.get("body", {}).get("storage", {}).get("value", "")
            return ConfluencePage(
                page_id=page_id,
                title=title,
                body_text=_strip_html(html_body),
            )
        except Exception as exc:
            logger.warning("Failed to fetch page %s: %s", page_id, exc)
            return None

    async def search(self, query: str, space_key: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search for pages matching a query string within a space."""
        cql = f'space="{space_key}" AND text~"{query}"'
        url = f"{self._base_url}/rest/api/content/search"
        try:
            async with httpx.AsyncClient(auth=self._auth, timeout=30) as client:
                resp = await client.get(url, params={"cql": cql, "limit": limit})
                resp.raise_for_status()
                return resp.json().get("results", [])
        except Exception as exc:
            logger.warning("Confluence search failed: %s", exc)
            return []