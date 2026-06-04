from datetime import datetime, timezone
from typing import Any
import hashlib
import json

import httpx


class ApifyError(RuntimeError):
    pass


def apify_config(settings_map: dict[str, str]) -> dict[str, Any]:
    return {
        "token": (settings_map.get("apify_api_token") or "").strip(),
        "timeout": int(settings_map.get("apify_timeout_seconds") or 90),
        "max_items": int(settings_map.get("apify_max_items") or 20),
        "enabled": settings_map.get("apify_enabled", "on") != "off",
    }


async def run_apify_source(source: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    if not config.get("enabled"):
        return []
    token = config.get("token")
    if not token:
        raise ApifyError("Apify API token не задан в настройках")
    actor_id = (source.get("actor_id") or source.get("url") or "").strip()
    if not actor_id:
        raise ApifyError("У источника Apify не задан actor_id")
    actor_input = build_actor_input(source)
    max_items = int(source.get("max_items") or config.get("max_items") or 20)
    timeout = int(source.get("timeout") or config.get("timeout") or 90)
    dataset_items = await run_actor_sync(actor_id, actor_input, token, timeout=timeout)
    return normalize_apify_items(flatten_apify_items(dataset_items)[:max_items], source)


def build_actor_input(source: dict[str, Any]) -> dict[str, Any]:
    raw_input = source.get("input")
    if isinstance(raw_input, dict):
        actor_input = dict(raw_input)
    else:
        actor_input = {}
    actor_id = (source.get("actor_id") or source.get("url") or "").strip().lower()
    query = (source.get("query") or "").strip()
    if query:
        if "google-news-scraper" in actor_id:
            actor_input.setdefault("searchQueries", [query])
        else:
            actor_input.setdefault("query", query)
            actor_input.setdefault("search", query)
            actor_input.setdefault("queries", [query])
            actor_input.setdefault("searchQueries", [query])
            actor_input.setdefault("search_terms", [query])
            actor_input.setdefault("keyword", query)
            actor_input.setdefault("keywords", [query])
    if source.get("max_items"):
        max_items = int(source["max_items"])
        actor_input.setdefault("maxItems", max_items)
        actor_input.setdefault("max_items", max_items)
        actor_input.setdefault("limit", max_items)
    return actor_input


async def run_actor_sync(
    actor_id: str,
    actor_input: dict[str, Any],
    token: str,
    timeout: int = 90,
) -> list[dict[str, Any]]:
    encoded_actor = actor_id.replace("/", "~")
    url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items"
    params = {"token": token, "clean": "true"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(url, params=params, json=actor_input)
    if response.status_code >= 400:
        raise ApifyError(f"Apify actor {actor_id} вернул {response.status_code}: {response.text[:500]}")
    data = response.json()
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items") or data.get("results") or data.get("data")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def normalize_apify_items(items: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        url = first_value(
            item,
            [
                "url",
                "link",
                "href",
                "articleUrl",
                "article_url",
                "sourceUrl",
                "source_url",
                "canonicalUrl",
                "canonical_url",
                "displayedUrl",
                "displayed_url",
            ],
        )
        title = first_value(item, ["title", "headline", "name", "text", "query", "pageTitle"])
        summary = first_value(
            item,
            ["summary", "description", "snippet", "content", "text", "body", "excerpt", "preview", "subtitle"],
        )
        if not title and summary:
            title = str(summary)[:140]
        if not url:
            url = f"apify://{source.get('actor_id') or source.get('name')}/{stable_item_id(item)}"
        if not title:
            continue
        normalized.append(
            {
                "url": str(url),
                "title": str(title).strip()[:180],
                "summary": str(summary or title).strip()[:3000],
                "source": f"Apify: {source['name']}",
                "collector_type": "apify",
                "actor_id": source.get("actor_id") or source.get("url") or "",
                "query": source.get("query") or "",
                "source_weight": float(source.get("weight", 1.0)),
                "published_at": parse_apify_date(
                    first_value(
                        item,
                        [
                            "publishedAt",
                            "published_at",
                            "publishedDate",
                            "date",
                            "createdAt",
                            "created_at",
                            "updatedAt",
                        ],
                    )
                )
                or datetime.now(timezone.utc).isoformat(),
            }
        )
    return normalized


def flatten_apify_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        children = nested_result_items(item)
        if children:
            flattened.extend(flatten_apify_items(children))
        else:
            flattened.append(item)
    return flattened


def nested_result_items(item: dict[str, Any]) -> list[dict[str, Any]]:
    result_keys = (
        "items",
        "results",
        "data",
        "organicResults",
        "organic_results",
        "newsResults",
        "news_results",
        "searchResults",
        "search_results",
        "posts",
        "articles",
        "stories",
        "repos",
        "repositories",
    )
    nested: list[dict[str, Any]] = []
    for key in result_keys:
        value = item.get(key)
        if isinstance(value, list):
            nested.extend(child for child in value if isinstance(child, dict))
        elif isinstance(value, dict):
            nested.extend(nested_result_items(value) or [value])
    return nested


def first_value(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def stable_item_id(item: dict[str, Any]) -> str:
    for key in ("id", "guid", "uid", "postId", "videoId"):
        if item.get(key):
            return str(item[key])
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse_apify_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        from dateutil import parser as date_parser

        parsed = date_parser.parse(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
