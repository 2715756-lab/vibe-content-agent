import re
from typing import Any

import httpx


AWESOME_OSINT_README_URL = "https://raw.githubusercontent.com/jivoi/awesome-osint/master/README.md"

HEADING_RE = re.compile(r"^##\s+(.*)$")
TOOL_RE = re.compile(r"^\s*[*-]\s+\[([^\]]+)\]\(([^)]+)\)\s*(?:[-—]\s*(.*))?\s*$")
SKIP_CATEGORIES = {
    "Table of Contents",
    "License",
    "Contributing",
    "Credits",
    "Related resources",
}
BLOCKED_CATEGORIES = {
    "Data Breach Search Engines",
    "Dark Web Search Engines",
    "People Investigations",
    "Phone Number Research",
    "Threat Actor Search",
    "Username Check",
}
BLOCKED_TERMS = (
    "track others",
    "tracking others",
    "location tracking",
    "monitor others",
    "real-time tracking",
    "channel joiner",
    "send messages quickly",
    "passport",
    "tax id",
    "nearby users",
    "position of nearby",
    "telegram scraper",
    "reconnaissance framework",
    "stalking",
    "dox",
    "doxx",
    "data breach",
    "breached",
    "leaked password",
    "phone number",
    "dark web",
    "malware",
)
PREFERRED_TERMS = (
    "ai",
    "news",
    "fact",
    "verification",
    "telegram",
    "github",
    "domain",
    "ip",
    "threat",
    "intelligence",
    "monitor",
    "image",
    "video",
    "rss",
    "api",
    "archive",
    "social",
)


async def fetch_osint_tools(url: str = AWESOME_OSINT_README_URL) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    return parse_awesome_osint(response.text)


def parse_awesome_osint(markdown: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    category = ""
    seen: set[tuple[str, str]] = set()
    for line in markdown.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            category = _clean_category(heading.group(1))
            continue
        match = TOOL_RE.match(line)
        if not match or not category or category in SKIP_CATEGORIES:
            continue
        name, url, description = match.groups()
        url = url.strip()
        if url.startswith("#"):
            continue
        key = (category, url)
        if key in seen:
            continue
        seen.add(key)
        tool = {
            "name": _clean_text(name),
            "url": url,
            "description": _clean_text(description or ""),
            "category": category,
            "source": "awesome-osint",
        }
        if not is_safe_editorial_tool(tool):
            continue
        tool["score"] = score_osint_tool(tool)
        tools.append(tool)
    return tools


def osint_tool_to_item(tool: dict[str, Any]) -> dict[str, Any]:
    description = tool.get("description") or "OSINT-инструмент из публичного каталога."
    category = tool.get("category") or "OSINT"
    return {
        "url": tool["url"],
        "title": f"OSINT: {tool['name']}",
        "summary": (
            f"{category}. {description} "
            "Безопасное применение для нашей лаборатории: проверка источников, "
            "фактчекинг, поиск публичных сигналов, defensive research и идеи для статей."
        ),
        "source": f"OSINT: {category}",
        "published_at": None,
        "score": tool.get("score", 0),
        "status": "new",
    }


def score_osint_tool(tool: dict[str, Any]) -> float:
    text = f"{tool.get('name', '')} {tool.get('description', '')} {tool.get('category', '')}".lower()
    score = 40.0
    for term in PREFERRED_TERMS:
        if term in text:
            score += 5.0
    if "github.com" in str(tool.get("url", "")).lower():
        score += 4.0
    if tool.get("description"):
        score += 3.0
    return min(score, 100.0)


def is_safe_editorial_tool(tool: dict[str, Any]) -> bool:
    category = str(tool.get("category") or "")
    if category in BLOCKED_CATEGORIES:
        return False
    text = f"{tool.get('name', '')} {tool.get('description', '')} {tool.get('url', '')}".lower()
    return not any(term in text for term in BLOCKED_TERMS)


def _clean_category(value: str) -> str:
    value = re.sub(r"\[↑\]\([^)]+\)", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("#", "").strip()
    return _clean_text(value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
