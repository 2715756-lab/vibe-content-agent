# vibe-content-agent

AI-агент для генерации и публикации контента в Telegram, VK, VC, Dzen, MAX.

## Инфраструктура

- **Хост:** Proxmox 192.168.1.69
- **Контейнер:** CT 102 (ai-ollama), Ubuntu 22.04, 192.168.1.112
- **Путь:** `/opt/vibe-content-agent/`
- **Python:** 3.12, venv в `/opt/vibe-content-agent/.venv/`
- **Сервис:** `vibe-content-agent` (systemd, uvicorn на 0.0.0.0:8088)
- **БД:** SQLite `/opt/vibe-content-agent/data/agent.sqlite3`

## Сеть — Xray-прокси для Telegram

Telegram API заблокирован провайдером. На контейнере установлен Xray-core (v26.3.27) как systemd-сервис `xray`. Он поднимает SOCKS5-прокси на `127.0.0.1:10808` и HTTP-прокси на `127.0.0.1:10809`, которые маршрутизируют трафик через VLESS+REALITY+XHTTP до сервера Happ VPN.

- Конфиг Xray: `/usr/local/etc/xray/config.json`
- Приложение использует `OUTBOUND_PROXY=socks5://127.0.0.1:10808` из `.env`
- Xray добавлен в автозагрузку, запускается независимо

### Обновление конфига Xray

Если подписка Happ поменяется (новый сервер, протокол), нужно:
1. Извлечь новый `connectedConfigJson` из plist Happ на Mac
2. Обновить `outbounds[0]` в `/usr/local/etc/xray/config.json`
3. `systemctl restart xray`

## Конфигурация приложения

`.env` лежит в `/opt/vibe-content-agent/.env`. Основные переменные:

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота alagent59_bot |
| `TELEGRAM_CHANNEL_ID` | Канал для публикации |
| `OUTBOUND_PROXY` | SOCKS5-прокси (сейчас Xray: socks5://127.0.0.1:10808) |
| `ADMIN_USERNAME` | Логин веб-интерфейса |
| `ADMIN_PASSWORD` | Пароль веб-интерфейса |

Часть настроек хранится в SQLite (`app_settings`): AI-провайдер (`ai_text_provider=custom`, `custom_text_base_url`, `custom_text_model`), Telegram-токен, VK-токен и т.д. Эти значения переопределяют `.env`.

### AI-провайдер

- `ai_text_provider = custom`
- `custom_text_base_url = https://inference-api.nousresearch.com/v1`
- `custom_text_model = nousresearch/hermes-4-70b`

## Публикация

- `publishers.py` — модуль публикации, использует `httpx.AsyncClient`
- Если задан `OUTBOUND_PROXY`, все HTTP-запросы идут через прокси
- Для SOCKS5 требуется пакет `socksio` (установлен в venv)

## Важные файлы

- `src/vibe_agent/config.py` — Pydantic-модель Settings, читает `.env`
- `src/vibe_agent/publishers.py` — функции publish_telegram, publish_vk, publish_max
- `src/vibe_agent/styles.py` — загрузка стилей из `config/styles/`
- `data/agent.sqlite3` — SQLite с настройками AI и платформ
