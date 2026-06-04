from datetime import datetime, timezone

from dateutil import parser as date_parser


def recency_score(published_at: str | None) -> float:
    if not published_at:
        return 0.2
    try:
        published = date_parser.parse(published_at)
    except (ValueError, TypeError):
        return 0.2
    age_hours = max((datetime.now(timezone.utc) - published).total_seconds() / 3600, 0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.7
    if age_hours <= 168:
        return 0.4
    return 0.15


def keyword_score(title: str, summary: str, keywords: list[str]) -> float:
    haystack = f"{title} {summary}".lower()
    matches = sum(1 for keyword in keywords if keyword and keyword in haystack)
    return min(matches / 4, 1.0)


def score_item(item: dict, keywords: list[str]) -> float:
    source_weight = float(item.get("source_weight", 1.0))
    score = (
        recency_score(item.get("published_at")) * 0.45
        + keyword_score(item.get("title", ""), item.get("summary", ""), keywords) * 0.4
        + min(source_weight / 1.5, 1.0) * 0.15
    )
    return round(score * 100, 2)
