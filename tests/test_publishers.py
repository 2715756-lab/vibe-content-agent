import asyncio

import httpx
import pytest

from vibe_agent.config import Settings
from vibe_agent.publishers import (
    MAX_TEXT_LIMIT,
    PublishError,
    publish,
    publish_max,
    publish_telegram,
)


def test_telegram_photo_caption_rejects_long_text(tmp_path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake")
    settings = Settings(telegram_bot_token="token", telegram_channel_id="@channel")

    with pytest.raises(PublishError, match="1024"):
        asyncio.run(
            publish_telegram(
                "x" * 1025,
                settings,
                image_path=str(image_path),
                bot_token="token",
                channel_ids=["@channel"],
            )
        )


def test_telegram_photo_missing_file_is_publish_error(tmp_path):
    settings = Settings(telegram_bot_token="token", telegram_channel_id="@channel")

    with pytest.raises(PublishError, match="Картинка"):
        asyncio.run(
            publish_telegram(
                "short post",
                settings,
                image_path=str(tmp_path / "missing.png"),
                bot_token="token",
                channel_ids=["@channel"],
            )
        )


def test_max_missing_credentials_raises_publish_error():
    # Regression: publish_max must accept `settings` (previously NameError on `settings`).
    settings = Settings()
    with pytest.raises(PublishError, match="MAX bot token"):
        asyncio.run(publish_max("hello", settings))


def test_max_rejects_text_over_limit():
    settings = Settings()
    with pytest.raises(PublishError, match="4000"):
        asyncio.run(
            publish_max(
                "x" * (MAX_TEXT_LIMIT + 1),
                settings,
                bot_token="token",
                chat_ids=["chat-1"],
            )
        )


def test_max_dispatch_via_publish_passes_settings(monkeypatch):
    """`publish("max", ...)` must pass `settings` through to `publish_max`."""
    settings = Settings()
    captured: dict = {}

    async def fake_publish_max(content, s, *, bot_token=None, chat_ids=None):
        captured["settings"] = s
        captured["bot_token"] = bot_token
        captured["chat_ids"] = chat_ids
        return {"ok": True}

    monkeypatch.setattr("vibe_agent.publishers.publish_max", fake_publish_max)
    result = asyncio.run(
        publish(
            "max",
            "post body",
            settings,
            overrides={"max_bot_token": "tok", "max_chat_ids": ["c1", "c2"]},
        )
    )
    assert result == {"ok": True}
    assert captured["settings"] is settings
    assert captured["bot_token"] == "tok"
    assert captured["chat_ids"] == ["c1", "c2"]


def test_max_sends_message_to_each_chat(monkeypatch):
    settings = Settings(outbound_proxy=None)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "headers": dict(request.headers)})
        return httpx.Response(200, json={"ok": True, "message_id": 123})

    transport = httpx.MockTransport(handler)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "vibe_agent.publishers.httpx.AsyncClient",
        lambda *args, **kwargs: real_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "proxy"}),
    )

    result = asyncio.run(
        publish_max(
            "Привет из MAX",
            settings,
            bot_token="Bearer secret",
            chat_ids=["chat-a", "chat-b"],
        )
    )
    assert result == {"ok": True, "results": [{"ok": True, "message_id": 123}, {"ok": True, "message_id": 123}]}
    assert len(sent) == 2
    assert sent[0]["headers"]["authorization"] == "Bearer secret"
    assert "chat_id=chat-a" in sent[0]["url"]
    assert "chat_id=chat-b" in sent[1]["url"]

