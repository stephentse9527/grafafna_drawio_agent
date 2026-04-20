"""
Configuration management for the Grafana Dashboard Agent.
Values are loaded from environment variables / .env file.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Config:
    # Confluence REST API credentials
    confluence_base_url: str = field(default_factory=lambda: os.getenv("CONFLUENCE_BASE_URL", ""))
    confluence_username: str = field(default_factory=lambda: os.getenv("CONFLUENCE_USERNAME", ""))
    confluence_api_token: str = field(default_factory=lambda: os.getenv("CONFLUENCE_API_TOKEN", ""))

    # Output directory
    output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("OUTPUT_DIR", "./output"))
    )

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty = OK)."""
        errors: list[str] = []
        if not self.confluence_base_url:
            errors.append(
                "CONFLUENCE_BASE_URL is not set. "
                "Set it in your .env file."
            )
        return errors
