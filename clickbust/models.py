"""Data models for Clickbust."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SiteConfig:
    """Configuration for a single site to monitor."""

    name: str
    rss_url: str
    site_url: str
    enabled: bool = True


@dataclass
class Article:
    """A single article with original and rewritten data."""

    title: str
    url: str
    site_name: str
    content_text: str = ""
    summary: str = ""
    image_url: str = ""  # thumbnail URL from the original article (og:image)
    body_images: list[dict] = field(default_factory=list)
    # ^ list of {"url": "...", "alt": "..."} from article body, used as fallback
    #   when og:image doesn't match the actual subject (reference-bait articles)
    rewritten_title: Optional[str] = None
    is_clickbait: bool = False
    published_date: Optional[datetime] = None
    article_id: str = ""  # unique slug for the generated page
    fetched_at: Optional[datetime] = None


@dataclass
class OutputConfig:
    """Configuration for the generated site."""

    dir: str = "output"
    site_title: str = "Clickbust — Rewritten Headlines"
    site_description: str = "Clickbait-free headlines from your favourite sites"
    base_url: str = "https://your-site.com"
    default_banner_url: str = ""  # Fallback OG image when article has no usable image
    max_articles: int = 50
    max_per_site: int = 20


@dataclass
class LLMConfig:
    """Configuration for the LLM API."""

    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    api_key: str = ""
    model: str = "openai/gpt-4o-mini"


@dataclass
class AppConfig:
    """Top-level application configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    sites: list[SiteConfig] = field(default_factory=list)
    output: OutputConfig = field(default_factory=OutputConfig)