from datetime import datetime, timezone

from vibe_agent.ranker import keyword_score, recency_score, score_item


def test_keyword_score_caps_at_one():
    score = keyword_score("OpenAI AI LLM agents", "Codex development", ["openai", "ai", "llm", "codex"])
    assert score == 1.0


def test_recency_score_for_fresh_item():
    assert recency_score(datetime.now(timezone.utc).isoformat()) == 1.0


def test_score_item_returns_percentage():
    item = {
        "title": "OpenAI releases coding agent",
        "summary": "AI development workflow",
        "source_weight": 1.2,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    assert 0 < score_item(item, ["openai", "ai", "development"]) <= 100
