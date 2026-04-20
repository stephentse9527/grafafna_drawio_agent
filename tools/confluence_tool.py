#!/usr/bin/env python3
"""
CLI tool for fetching Confluence pages via the REST API.

Usage
-----
  # List all pages in a space (outputs JSON array to stdout)
  python tools/confluence_tool.py list SPACE_KEY

  # Read the plain-text content of a single page (outputs to stdout)
  python tools/confluence_tool.py read PAGE_ID

Credentials are read from .env (CONFLUENCE_BASE_URL, CONFLUENCE_USERNAME,
CONFLUENCE_API_TOKEN).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure project root is on the path when running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agent.config import Config
from agent.tools.confluence import ConfluenceClient


def _get_client() -> ConfluenceClient:
    cfg = Config()
    errors = cfg.validate()
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    return ConfluenceClient(
        rest_base_url=cfg.confluence_base_url,
        rest_username=cfg.confluence_username,
        rest_api_token=cfg.confluence_api_token,
    )


async def cmd_list(space_key: str) -> None:
    client = _get_client()
    pages = await client.list_pages(space_key)
    output = [
        {"id": str(p.get("id", "")), "title": p.get("title", "")}
        for p in pages
    ]
    print(json.dumps(output, indent=2, ensure_ascii=False))


async def cmd_read(page_id: str) -> None:
    client = _get_client()
    page = await client.get_page(page_id)
    if page is None:
        print(f"ERROR: Page {page_id} not found or could not be fetched.", file=sys.stderr)
        sys.exit(1)
    print(f"Title: {page.title}\n")
    print(page.body_text)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="confluence_tool",
        description="Fetch pages from a Confluence space via REST API.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list", help="List all pages in a space")
    list_p.add_argument("space_key", help="Confluence space key (e.g. MYAPP)")

    read_p = sub.add_parser("read", help="Read the content of a page")
    read_p.add_argument("page_id", help="Numeric Confluence page ID")

    args = parser.parse_args()

    if args.cmd == "list":
        asyncio.run(cmd_list(args.space_key))
    elif args.cmd == "read":
        asyncio.run(cmd_read(args.page_id))


if __name__ == "__main__":
    main()
