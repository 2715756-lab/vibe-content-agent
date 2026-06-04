# Перенос Vibe Content Agent с VPS на локальный сервер

Цель: успеть забрать с VPS код, базу, медиа, настройки и документацию, а затем поднять агент на домашнем сервере или новом VPS без потери статей, черновиков, источников, стилей, журнала публикаций и блога.

## Что важно сохранить

| Путь | Что внутри | Критичность |
|---|---|---|
| `data/agent.sqlite3` | база статей, черновиков, публикаций, настроек, стилей, задач | высокая |
| `data/media/` | картинки статей и обложки | высокая |
| `config/` | источники, стиль, YAML-настройки | высокая |
| `content/` | wiki/публичные материалы проекта | средняя |
| `docs/` | инструкции, help, manual, SEO/стратегии | средняя |
| `.env` | локальные переменные запуска | высокая, но файл нельзя публиковать |
| `src/`, `tests/`, `pyproject.toml` | код приложения | высокая |

## Рекомендуемая схема

Лучший вариант для домашнего сервера: Proxmox LXC/VM + systemd + Cloudflare Tunnel.

Почему так:

- приложение продолжает слушать только `127.0.0.1:8088` внутри сервера;
- наружу открывается только Cloudflare Tunnel, без проброса портов на роутере;
- домен `agent.gazon59.ru` можно переключить с VPS на tunnel;
- админка остается под Basic Auth, публичные страницы `/blog`, `/projects`, `/rss.xml` доступны людям и ботам.

Если домашний интернет нестабилен, лучше новый VPS. Домашний сервер тогда можно оставить для разработки, тестов и приватных задач, а публичный блог держать на VPS.

## Срочный бэкап с текущего VPS

Когда SSH на VPS отвечает:

```bash
cd "/path/to/vibe-content-agent"
VPS_HOST=OLD_VPS_IP ./scripts/backup_vps.sh
```

Скрипт создаст архив вида:

```text
backups/vps-migration-YYYYMMDD-HHMMSS/vibe-content-agent.tgz
```

Если SSH зависает на баннере или таймаутится, не ждать бесконечно. Повторить позже или сначала поднять локальную копию из текущей рабочей папки, а серверный бэкап забрать отдельно.

## Подготовка домашнего сервера

Минимальная конфигурация:

- Ubuntu 24.04 или Debian 12;
- 2 CPU, 2-4 GB RAM;
- 20+ GB диска;
- доступ по SSH;
- Python 3.11+;
- отдельный системный пользователь `vibe-agent`.

На Proxmox лучше создать отдельную LXC/VM, чтобы не смешивать агента с другими сервисами.

## Разворот из архива на Linux

Скопировать архив на новый сервер:

```bash
scp backups/vps-migration-YYYYMMDD-HHMMSS/vibe-content-agent.tgz root@NEW_SERVER:/root/
scp scripts/install_on_linux_server.sh root@NEW_SERVER:/root/
```

На новом сервере:

```bash
sudo bash /root/install_on_linux_server.sh /root/vibe-content-agent.tgz
```

Если скрипт уже лежит внутри архива, можно сначала распаковать архив вручную и запустить локальную копию:

```bash
mkdir -p /opt/vibe-content-agent
tar -xzf /root/vibe-content-agent.tgz -C /opt/vibe-content-agent
sudo bash /opt/vibe-content-agent/scripts/install_on_linux_server.sh /root/vibe-content-agent.tgz
```

Проверка:

```bash
systemctl status vibe-content-agent
curl http://127.0.0.1:8088/health
```

Можно сделать то же самое одной командой с Mac, когда известен IP новой ноды:

```bash
TARGET_HOST=192.168.1.50 ./scripts/restore_to_remote_server.sh backups/vps-migration-YYYYMMDD-HHMMSS/vibe-content-agent.tgz
```

Для нового VPS:

```bash
TARGET_HOST=NEW_VPS_IP TARGET_USER=root ./scripts/restore_to_remote_server.sh backups/vps-migration-YYYYMMDD-HHMMSS/vibe-content-agent.tgz
```

## Cloudflare Tunnel для домашнего сервера

На сервере установить `cloudflared`, затем создать tunnel для домена `agent.gazon59.ru`.

Логика маршрута:

```text
https://agent.gazon59.ru -> Cloudflare Tunnel -> http://127.0.0.1:8088
```

Плюсы:

- не нужен белый IP;
- не нужен проброс порта 80/443;
- Cloudflare продолжает защищать публичный сайт;
- можно быстро переключиться обратно на VPS.

## Что проверить после переезда

1. `/health` отвечает `ok`.
2. `/blog` открывает публичные статьи.
3. `/projects` открывает проекты.
4. `/rss.xml` отдает RSS для Дзена.
5. `/admin/control` закрыт авторизацией.
6. Генерация картинки работает.
7. Рерайт работает выбранным AI-провайдером.
8. Публикация в Telegram отправляет картинку и текст одним сообщением.
9. В журнале публикаций появляются новые записи.
10. Старые медиа открываются по URL.

## Быстрый откат

До смены DNS старый VPS лучше не выключать.

Если новый сервер не работает:

1. вернуть DNS `agent.gazon59.ru` на старый VPS;
2. перезапустить `vibe-content-agent` на VPS;
3. проверить `https://agent.gazon59.ru/health`.

## Что нужно от владельца сервера

Для финального переноса нужны:

- IP или hostname домашней ноды;
- SSH-пользователь;
- тип сервера: Proxmox LXC, VM, bare metal или новый VPS;
- будет ли домен оставаться `agent.gazon59.ru`;
- хотим ли открывать сайт наружу через Cloudflare Tunnel или временно оставить только локальный доступ.

## Текущий статус

Если текущий VPS иногда не отвечает по SSH и соединение обрывается на этапе banner exchange, первый приоритет - поймать окно доступности и снять архив `vibe-content-agent.tgz`.
