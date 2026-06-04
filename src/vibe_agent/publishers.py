import httpx

from vibe_agent.config import Settings

TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
MAX_TEXT_LIMIT = 4000


class PublishError(RuntimeError):
    pass


def split_channel_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


async def publish_telegram(
    content: str,
    settings: Settings,
    image_path: str | None = None,
    bot_token: str | None = None,
    channel_ids: list[str] | None = None,
) -> dict:
    token = bot_token or settings.telegram_bot_token
    channels = channel_ids or split_channel_ids(settings.telegram_channel_id)
    if not token or not channels:
        raise PublishError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID are required")
    results = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for channel_id in channels:
                if image_path:
                    if len(content) > TELEGRAM_PHOTO_CAPTION_LIMIT:
                        raise PublishError(
                            "Telegram не позволяет подпись к картинке длиннее 1024 символов. "
                            "Чтобы Дзен забрал картинку и статью одним постом, укороти Telegram-версию "
                            "до 900-1000 символов и оставь заголовок первой строкой."
                        )
                    photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                    try:
                        image_file = open(image_path, "rb")
                    except OSError as exc:
                        raise PublishError(f"Картинка для Telegram недоступна: {image_path}") from exc
                    with image_file:
                        response = await client.post(
                            photo_url,
                            data={
                                "chat_id": channel_id,
                                "caption": content,
                            },
                            files={"photo": image_file},
                        )
                else:
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    response = await client.post(
                        url,
                        json={
                            "chat_id": channel_id,
                            "text": content,
                            "disable_web_page_preview": False,
                        },
                    )
                response.raise_for_status()
                results.append(response.json())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise PublishError(f"Telegram API ответил ошибкой: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise PublishError("Telegram API не ответил за 20 секунд. Попробуй ещё раз чуть позже.") from exc
    except httpx.RequestError as exc:
        raise PublishError(f"Telegram API сейчас недоступен: {exc}") from exc
    if len(results) == 1:
        return results[0]
    return {"ok": True, "results": results}


async def publish_vk(
    content: str,
    settings: Settings,
    access_token: str | None = None,
    owner_id: str | None = None,
) -> dict:
    token = access_token or settings.vk_access_token
    target_owner_id = owner_id or settings.vk_owner_id
    if not token or not target_owner_id:
        raise PublishError("VK_ACCESS_TOKEN and VK_OWNER_ID are required")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.vk.com/method/wall.post",
                data={
                    "access_token": token,
                    "owner_id": target_owner_id,
                    "message": content,
                    "v": "5.199",
                },
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise PublishError(f"VK API ответил ошибкой: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise PublishError("VK API не ответил за 20 секунд. Попробуй ещё раз чуть позже.") from exc
    except httpx.RequestError as exc:
        raise PublishError(f"VK API сейчас недоступен: {exc}") from exc
    data = response.json()
    if "error" in data:
        raise PublishError(str(data["error"]))
    return data


async def publish_max(
    content: str,
    bot_token: str | None = None,
    chat_ids: list[str] | None = None,
) -> dict:
    token = bot_token
    chats = chat_ids or []
    if not token or not chats:
        raise PublishError("MAX bot token and MAX chat/channel ID are required")
    if len(content) > MAX_TEXT_LIMIT:
        raise PublishError("MAX принимает текст сообщения до 4000 символов. Сократи версию для MAX.")
    results = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for chat_id in chats:
                response = await client.post(
                    "https://platform-api.max.ru/messages",
                    params={"chat_id": chat_id},
                    headers={
                        "Authorization": token,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": content,
                        "format": "markdown",
                        "disable_link_preview": False,
                        "notify": True,
                    },
                )
                response.raise_for_status()
                results.append(response.json())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise PublishError(f"MAX API ответил ошибкой: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise PublishError("MAX API не ответил за 20 секунд. Попробуй ещё раз чуть позже.") from exc
    except httpx.RequestError as exc:
        raise PublishError(f"MAX API сейчас недоступен: {exc}") from exc
    if len(results) == 1:
        return results[0]
    return {"ok": True, "results": results}


async def publish(
    platform: str,
    content: str,
    settings: Settings,
    image_path: str | None = None,
    overrides: dict | None = None,
) -> dict:
    overrides = overrides or {}
    if platform == "telegram":
        return await publish_telegram(
            content,
            settings,
            image_path,
            bot_token=overrides.get("telegram_bot_token"),
            channel_ids=overrides.get("telegram_channel_ids"),
        )
    if platform == "vk":
        return await publish_vk(
            content,
            settings,
            access_token=overrides.get("vk_access_token"),
            owner_id=overrides.get("vk_owner_id"),
        )
    if platform == "max":
        return await publish_max(
            content,
            bot_token=overrides.get("max_bot_token"),
            chat_ids=overrides.get("max_chat_ids"),
        )
    if platform in {"vc", "dzen"}:
        return {"status": "manual", "message": "Черновик готов для ручной публикации."}
    raise PublishError(f"Unknown platform: {platform}")
