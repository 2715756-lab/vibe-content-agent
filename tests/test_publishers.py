import asyncio

import pytest

from vibe_agent.config import Settings
from vibe_agent.publishers import PublishError, publish_telegram


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
