from __future__ import annotations

import httpx

from vibe_agent.config import Settings
from vibe_agent.publishers import publish
from vibe_agent.service import ContentAgent


HELP_TEXT = """Команды AI-редактора:
/start — подключить этот чат к управлению
/help — список команд
/status — статус агента
/run — запустить поиск новостей
/topics — показать топ тем
/draft <id> [telegram|vk|vc|dzen] — создать черновик
/publish <draft_id> — опубликовать черновик
/queue — показать отложенные публикации
"""


class TelegramControl:
    def __init__(self, settings: Settings, agent: ContentAgent):
        self.settings = settings
        self.agent = agent
        self.timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)

    async def poll_once(self) -> None:
        if not self.bot_token:
            return
        offset = self.agent.storage.get_setting("telegram_update_offset", "0") or "0"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self.api_url("getUpdates"),
                    params={"offset": int(offset), "timeout": 0, "allowed_updates": '["message"]'},
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        return
                    raise
                data = response.json()
        except httpx.TimeoutException:
            self.agent.storage.set_setting("telegram_control_status", "timeout")
            return
        except httpx.NetworkError:
            self.agent.storage.set_setting("telegram_control_status", "network_error")
            return
        self.agent.storage.set_setting("telegram_control_status", "ok")
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            self.agent.storage.set_setting("telegram_update_offset", str(update["update_id"] + 1))
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))
            if text:
                await self.handle_command(chat_id, text)

    async def handle_command(self, chat_id: str, text: str) -> None:
        if not self.is_allowed(chat_id, text):
            await self.send_message(chat_id, "Этот чат не подключен к управлению агентом.")
            return

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]

        if command == "/start":
            self.agent.storage.set_setting("telegram_control_chat_id", chat_id)
            await self.send_message(chat_id, "Готово. Этот чат подключен к управлению AI-редактором.\n\n" + HELP_TEXT)
        elif command == "/help":
            await self.send_message(chat_id, HELP_TEXT)
        elif command == "/status":
            await self.send_message(chat_id, self.status_text())
        elif command == "/run":
            result = await self.agent.collect_and_rank()
            await self.send_message(
                chat_id,
                f"Поиск завершён.\nНайдено: {result['fetched']}\nНовых в базе: {result['inserted']}",
            )
        elif command == "/topics":
            await self.send_message(chat_id, self.topics_text())
        elif command == "/draft":
            await self.create_draft(chat_id, args)
        elif command == "/publish":
            await self.publish_draft(chat_id, args)
        elif command == "/queue":
            await self.send_message(chat_id, self.queue_text())
        else:
            await self.send_message(chat_id, "Не знаю такую команду.\n\n" + HELP_TEXT)

    def is_allowed(self, chat_id: str, text: str) -> bool:
        configured = (
            self.agent.storage.get_setting("telegram_review_chat_id")
            or self.settings.telegram_review_chat_id
        )
        stored = self.agent.storage.get_setting("telegram_control_chat_id")
        if configured:
            return chat_id == str(configured)
        if stored:
            return chat_id == stored
        return text.split()[0].split("@")[0].lower() == "/start"

    def status_text(self) -> str:
        items = self.agent.storage.list_items(limit=1)
        publications = self.agent.storage.list_publications(limit=1)
        queue = self.agent.storage.list_publication_queue(limit=5)
        latest = items[0]["title"] if items else "тем пока нет"
        return (
            "AI-редактор работает.\n"
            f"Последняя тема: {latest}\n"
            f"Последняя публикация: {'есть' if publications else 'нет'}\n"
            f"В очереди: {len(queue)}"
        )

    def topics_text(self) -> str:
        items = self.agent.storage.list_items(limit=5)
        if not items:
            return "Тем пока нет. Запусти /run."
        lines = ["Топ тем:"]
        for item in items:
            lines.append(f"{item['id']}. {item['title']} ({item['score']:.0f})")
        lines.append("\nСоздать черновик: /draft <id> telegram")
        return "\n".join(lines)

    async def create_draft(self, chat_id: str, args: list[str]) -> None:
        if not args or not args[0].isdigit():
            await self.send_message(chat_id, "Формат: /draft <id темы> [telegram|vk|vc|dzen]")
            return
        platform = args[1] if len(args) > 1 else "telegram"
        try:
            draft = await self.agent.draft(int(args[0]), platform)
        except Exception as exc:  # noqa: BLE001 - command should answer with a readable error.
            await self.send_message(chat_id, f"Не получилось создать черновик: {exc}")
            return
        preview = draft["content"][:1200]
        await self.send_message(
            chat_id,
            f"Черновик #{draft['draft_id']} для {platform} создан.\n\n{preview}\n\nОпубликовать: /publish {draft['draft_id']}",
        )

    async def publish_draft(self, chat_id: str, args: list[str]) -> None:
        if not args or not args[0].isdigit():
            await self.send_message(chat_id, "Формат: /publish <id черновика>")
            return
        draft = self.agent.storage.get_draft(int(args[0]))
        if not draft:
            await self.send_message(chat_id, "Черновик не найден.")
            return
        image = self.agent.storage.get_latest_media_asset(draft["id"])
        image_path = image["path"] if image else None
        try:
            result = await publish(
                draft["platform"],
                draft["content"],
                self.settings,
                image_path=image_path,
                overrides=self.publish_overrides(),
            )
            status = "published" if draft["platform"] in {"telegram", "vk"} else "ready"
            self.agent.storage.save_publication(
                draft_id=draft["id"],
                item_id=draft["item_id"],
                platform=draft["platform"],
                status=status,
                content=draft["content"],
                response=result,
                image_path=image_path,
            )
            self.agent.storage.update_draft_status(draft["id"], status)
        except Exception as exc:  # noqa: BLE001
            await self.send_message(chat_id, f"Публикация не удалась: {exc}")
            return
        await self.send_message(chat_id, f"Готово. Черновик #{draft['id']} отправлен.")

    def publish_overrides(self) -> dict:
        values = self.agent.storage.get_settings_map(
            ["telegram_bot_token", "telegram_channel_ids", "vk_access_token", "vk_owner_id"]
        )
        telegram_channels = values.get("telegram_channel_ids") or self.settings.telegram_channel_id or ""
        return {
            "telegram_bot_token": values.get("telegram_bot_token") or self.settings.telegram_bot_token,
            "telegram_channel_ids": [
                item.strip()
                for item in telegram_channels.replace("\n", ",").split(",")
                if item.strip()
            ],
            "vk_access_token": values.get("vk_access_token") or self.settings.vk_access_token,
            "vk_owner_id": values.get("vk_owner_id") or self.settings.vk_owner_id,
        }

    def queue_text(self) -> str:
        queued = self.agent.storage.list_publication_queue(limit=10)
        if not queued:
            return "Очередь пустая."
        lines = ["Очередь публикаций:"]
        for item in queued:
            lines.append(
                f"#{item['id']} {item['scheduled_at']} · {item['platform']} · {item['status']} · {item['item_title'][:80]}"
            )
        return "\n".join(lines)

    async def send_message(self, chat_id: str, text: str) -> None:
        if not self.bot_token:
            return
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(
                    self.api_url("sendMessage"),
                    json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            self.agent.storage.set_setting("telegram_control_status", "network_error")

    def api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    @property
    def bot_token(self) -> str | None:
        return self.agent.storage.get_setting("telegram_bot_token") or self.settings.telegram_bot_token
