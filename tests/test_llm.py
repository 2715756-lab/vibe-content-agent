from vibe_agent.llm import articles_are_same, clean_article_text, normalize_api_key


def test_clean_article_text_removes_service_wrappers():
    text = """Версия в стиле автора для telegram

Задача на рерайт: научный

Черновик для telegram

Готовый заголовок

Текст статьи.

Что проверить перед публикацией
- факт
- дата
"""
    assert clean_article_text(text) == "Готовый заголовок\n\nТекст статьи."


def test_clean_article_text_removes_reasoning_tags():
    text = """обрыв мысли
</think>
<think>
надо сначала подумать
</think>
**Сделал вики для команды**

Текст статьи.
</assistant>
"""
    assert clean_article_text(text) == "Сделал вики для команды\n\nТекст статьи."


def test_clean_article_text_removes_markdown_formatting():
    text = """**Как я построил AI-базу знаний**
*Артем, 2026-06-01*

---

### 1. Хук: зачем вообще строить AI-базу знаний?

- Первый тезис без звёздочки.
- Второй тезис без звёздочки.
"""
    assert clean_article_text(text) == (
        "Как я построил AI-базу знаний\n\n"
        "Зачем вообще строить AI-базу знаний?\n\n"
        "Первый тезис без звёздочки.\n"
        "Второй тезис без звёздочки."
    )


def test_normalize_api_key_rejects_pasted_non_ascii_text():
    assert normalize_api_key("ключ: не тот текст") is None


def test_normalize_api_key_extracts_openrouter_key():
    assert normalize_api_key("OpenRouter key: sk-or-v1-test_123") == "sk-or-v1-test_123"


def test_articles_are_same_detects_identical_rewrite():
    assert articles_are_same("Один текст\n\nВторой абзац.", "Один текст Второй абзац.")
    assert not articles_are_same("Один текст.", "Совсем другой материал.")


def test_is_invalid_model_response_catches_safety_text():
    from vibe_agent.llm import is_invalid_model_response

    # Exact safety replies
    assert is_invalid_model_response("User Safety: safe")
    assert is_invalid_model_response("user safety: unsafe")
    assert is_invalid_model_response("Safety categories: harassment")
    assert is_invalid_model_response("Blocked reason: sensitive topic")
    assert is_invalid_model_response("Finish reason: safety")

    # Valid article-like text should NOT be flagged
    assert not is_invalid_model_response(
        "Как я построил AI-базу знаний\n\nСначала выбрал модель..."
    )
    assert not is_invalid_model_response(
        "Сегодня поговорим о новом фреймворке. Он решает старую проблему."
    )
    assert not is_invalid_model_response(
        "User Safety: safe is important to remember"  # not a pure safety response
    )


def test_is_invalid_model_response_catches_empty_and_short():
    from vibe_agent.llm import is_invalid_model_response

    assert is_invalid_model_response("")
    assert is_invalid_model_response("   ")
    assert is_invalid_model_response("\n\n  \n")
