from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from vibe_agent.apify import run_apify_source


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def parse_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return date_parser.parse(str(value)).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def load_sources(path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("sources", [])


def save_sources(path, sources: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"sources": sources}, fh, allow_unicode=True, sort_keys=False)


def add_source(path, source: dict[str, Any]) -> None:
    sources = load_sources(path)
    normalized = normalize_source(source)
    sources = [
        existing
        for existing in sources
        if existing.get("url") != normalized.get("url") or existing.get("name") != normalized.get("name")
    ]
    sources.append(normalized)
    save_sources(path, sources)


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    source_type = source.get("type", "rss").strip().lower()
    name = source.get("name", "").strip() or "Untitled source"
    weight = float(source.get("weight") or 1.0)
    if source_type == "telegram":
        channel = normalize_telegram_channel(source.get("url") or source.get("channel") or name)
        return {
            "name": name,
            "type": "telegram",
            "url": f"https://t.me/s/{channel}",
            "channel": channel,
            "weight": weight,
        }
    if source_type == "website":
        return {
            "name": name,
            "type": "website",
            "url": source.get("url", "").strip(),
            "weight": weight,
        }
    if source_type == "apify_actor":
        actor_id = (source.get("actor_id") or source.get("url") or "").strip()
        normalized = {
            "name": name,
            "type": "apify_actor",
            "url": actor_id,
            "actor_id": actor_id,
            "query": (source.get("query") or "").strip(),
            "weight": weight,
        }
        if source.get("max_items"):
            normalized["max_items"] = int(source["max_items"])
        if isinstance(source.get("input"), dict):
            normalized["input"] = source["input"]
        return normalized
    return {
        "name": name,
        "type": "rss",
        "url": source.get("url", "").strip(),
        "weight": weight,
    }


def normalize_telegram_channel(value: str) -> str:
    raw = value.strip()
    if raw.startswith("@"):
        return raw[1:]
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.netloc.endswith("t.me"):
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0] == "s":
            parts = parts[1:]
        if parts:
            return parts[0]
    return raw.strip("/")


async def fetch_rss(source: dict[str, Any]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(source["url"])
        response.raise_for_status()
    feed = feedparser.parse(response.text)
    items: list[dict[str, Any]] = []
    for entry in feed.entries[:30]:
        published = parse_date(
            entry.get("published")
            or entry.get("updated")
            or entry.get("created")
            or datetime.now(timezone.utc).isoformat()
        )
        items.append(
            {
                "url": entry.get("link"),
                "title": clean_html(entry.get("title")) or "Untitled",
                "summary": clean_html(entry.get("summary")),
                "source": source["name"],
                "source_weight": float(source.get("weight", 1.0)),
                "published_at": published,
            }
        )
    return [item for item in items if item.get("url")]


async def fetch_telegram(source: dict[str, Any]) -> list[dict[str, Any]]:
    channel = source.get("channel") or normalize_telegram_channel(source["url"])
    url = source.get("url") or f"https://t.me/s/{channel}"
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 VibeContentAgent/0.1"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    items: list[dict[str, Any]] = []
    for message in soup.select(".tgme_widget_message")[:30]:
        text_node = message.select_one(".tgme_widget_message_text")
        text = text_node.get_text(" ", strip=True) if text_node else ""
        if not text:
            continue
        link = message.get("data-post")
        message_url = f"https://t.me/{link}" if link else url
        title = make_title(text)
        time_node = message.select_one("time")
        published = parse_date(time_node.get("datetime")) if time_node else None
        items.append(
            {
                "url": message_url,
                "title": title,
                "summary": text,
                "source": source["name"],
                "source_weight": float(source.get("weight", 1.0)),
                "published_at": published or datetime.now(timezone.utc).isoformat(),
            }
        )
    return items


async def fetch_website(source: dict[str, Any]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 VibeContentAgent/0.1"},
    ) as client:
        response = await client.get(source["url"])
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    base_url = str(response.url)
    base = urlparse(base_url)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for anchor in soup.select("a[href]"):
        title = clean_html(anchor.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        if title.lower().startswith("перейти к"):
            continue
        url = urljoin(base_url, anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.netloc != base.netloc:
            continue
        if parsed.path.rstrip("/") == base.path.rstrip("/"):
            continue
        if parsed.path in {"", "/"} or url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title[:160],
                "summary": title,
                "source": source["name"],
                "source_weight": float(source.get("weight", 1.0)),
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(items) >= 30:
            break
    return items


def make_title(text: str) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
    return first_line[:110] + ("..." if len(first_line) > 110 else "")


async def collect_sources(path, apify_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for source in load_sources(path):
        try:
            if source.get("type") == "telegram":
                collected.extend(await fetch_telegram(source))
            elif source.get("type") == "website":
                collected.extend(await fetch_website(source))
            elif source.get("type") == "apify_actor":
                collected.extend(await run_apify_source(source, apify_config or {}))
            elif source.get("type") == "rss":
                collected.extend(await fetch_rss(source))
        except Exception as exc:  # noqa: BLE001 - collection should continue per source.
            collected.append(
                {
                    "url": f"error://{source['name']}",
                    "title": f"Source error: {source['name']}",
                    "summary": str(exc),
                    "source": "system",
                    "source_weight": 0,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                }
            )
    return collected
