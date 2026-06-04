from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus
from uuid import uuid4
import asyncio
import base64
import json
import re
import secrets

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as date_parser
from fastapi import Cookie, FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from vibe_agent.apify import ApifyError, apify_config, run_apify_source
from vibe_agent.config import get_settings
from vibe_agent.collector import add_source, load_sources
from vibe_agent.llm import clean_article_text, normalize_api_key, rewrite_draft
from vibe_agent.media import (
    ImageGenerationError,
    generate_image_for_topic,
    generate_video_for_topic,
    media_url,
    safe_filename,
)
from vibe_agent.osint_tools import fetch_osint_tools, osint_tool_to_item
from vibe_agent.publishers import PublishError, publish
from vibe_agent.ranker import score_item
from vibe_agent.service import ContentAgent
from vibe_agent.styles import append_to_style, list_styles, read_style, save_style
from vibe_agent.telegram_control import TelegramControl

settings = get_settings()
agent = ContentAgent(settings)
scheduler = AsyncIOScheduler()
telegram_control = TelegramControl(settings, agent)
search_state = {
    "running": False,
    "progress": 0,
    "stage": "ожидание",
    "fetched": 0,
    "inserted": 0,
    "error": "",
    "updated_at": "",
}
viral_state = {
    "running": False,
    "progress": 0,
    "stage": "ожидание",
    "ideas": [],
    "error": "",
    "updated_at": "",
}


def active_style_text() -> str:
    active_style = agent.storage.get_setting("active_style", "base") or "base"
    return read_style(settings, active_style) + agent.storage.style_memory_text()


def build_research_report(draft: dict, item: dict, content: str, extra_question: str = "") -> str:
    clean_content = clean_article_text(content) or clean_article_text(draft["content"])
    source = item.get("url") or ""
    title = item.get("title") or first_non_empty_line(clean_content) or "Тема"
    summary = item.get("summary") or "Описание источника отсутствует, нужна ручная проверка."
    angles = [
        "Практический угол: что это меняет для автора, разработчика или малого проекта.",
        "Скептический угол: где здесь хайп, ограничения, стоимость и слабые места.",
        "Личный угол: как это можно проверить в нашей лаборатории AI на миллион.",
    ]
    if "github.com" in source:
        angles.insert(0, "GitHub-угол: почему репозиторий растёт, что внутри README, какие issues/PR показывают реальный спрос.")
    if "OSINT:" in str(item.get("source") or ""):
        angles.insert(0, "OSINT-угол: как инструмент помогает проверять источники, находить публичные сигналы и не превращаться в серую зону.")
    questions = [
        "Что является первоисточником и открывается ли ссылка?",
        "Какие цифры, даты и утверждения нужно перепроверить перед публикацией?",
        "Что в этом материале даст читателю практическую пользу уже сегодня?",
        "Какой вывод автор может сделать от себя, без пересказа чужого README?",
    ]
    if extra_question.strip():
        questions.insert(0, extra_question.strip())
    return clean_article_text(
        f"""
Research Report

Тема

{title}

Источник

{source or "Источник не указан"}

Краткая суть

{summary}

Почему это может зайти

Тема связана с текущим спросом на AI-инструменты, локальные рабочие места, автоматизацию контента и самостоятельную разработку. Для аудитории канала важна не новость сама по себе, а прикладной вывод: что можно попробовать, внедрить или использовать как идею для своего проекта.

Углы подачи

{chr(10).join(angles)}

Что проверить перед публикацией

{chr(10).join(questions)}

Риски

Не раздувать хайп без фактов. Не обещать результат, если он зависит от железа, токенов, API-лимитов или качества модели. Не копировать README как статью; нужен авторский вывод и понятный сценарий применения.

Рекомендуемый следующий шаг

Сделать черновик статьи через AI Compare: один вариант с сильным хуком, один аналитический, один короткий для Telegram. Победивший вариант дополнить личным выводом и ссылкой на источник.
        """
    )


def first_non_empty_line(content: str) -> str:
    for line in content.splitlines():
        if line.strip():
            return line.strip()
    return ""


def safe_return_path(value: str, fallback: str = "/admin/control") -> str:
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return fallback
    return value


async def scheduled_run() -> None:
    await run_search_with_progress()


async def run_search_with_progress() -> dict:
    if search_state["running"]:
        return {
            "fetched": search_state["fetched"],
            "inserted": search_state["inserted"],
            "already_running": True,
        }
    search_state.update(
        {
            "running": True,
            "progress": 8,
            "stage": "подключаю источники",
            "fetched": 0,
            "inserted": 0,
            "error": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        await asyncio.sleep(0.2)
        search_state.update({"progress": 38, "stage": "забираю новости и посты"})
        result = await agent.collect_and_rank()
        search_state.update(
            {
                "progress": 86,
                "stage": "ранжирую темы",
                "fetched": result["fetched"],
                "inserted": result["inserted"],
            }
        )
        await asyncio.sleep(0.2)
        search_state.update({"progress": 100, "stage": "готово"})
        return result
    except Exception as exc:
        search_state.update({"progress": 100, "stage": "ошибка", "error": str(exc)})
        raise
    finally:
        search_state["running"] = False
        search_state["updated_at"] = datetime.now(timezone.utc).isoformat()


async def run_viral_research_with_progress() -> dict:
    if viral_state["running"]:
        return {"ideas": viral_state["ideas"], "already_running": True}
    viral_state.update(
        {
            "running": True,
            "progress": 8,
            "stage": "собираю свежие темы",
            "ideas": [],
            "error": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        viral_state.update({"progress": 22, "stage": "обновляю источники"})
        await agent.collect_and_rank()
        items = agent.storage.list_items(limit=120)
        viral_state.update({"progress": 35, "stage": "ищу сигналы вирусности"})
        await asyncio.sleep(0.1)
        ideas = rank_viral_ideas(items)
        viral_state.update({"progress": 82, "stage": "упаковываю аргументы", "ideas": ideas})
        await asyncio.sleep(0.1)
        viral_state.update({"progress": 100, "stage": "готово"})
        return {"ideas": ideas}
    except Exception as exc:
        viral_state.update({"progress": 100, "stage": "ошибка", "error": str(exc)})
        raise
    finally:
        viral_state["running"] = False
        viral_state["updated_at"] = datetime.now(timezone.utc).isoformat()


def rank_viral_ideas(items: list[dict]) -> list[dict]:
    scored = [viral_idea_from_item(item) for item in items if item.get("status") != "error"]
    scored.sort(key=lambda item: item["viral_score"], reverse=True)
    return scored[:10]


def viral_idea_from_item(item: dict) -> dict:
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}".lower()
    base_score = min(float(item.get("score") or 0), 100.0)
    hype_signals = {
        "ai": ("ai", "ии", "llm", "агент", "agent", "openai", "claude", "gemini", "qwen", "deepseek"),
        "tools": ("инструмент", "tool", "github", "код", "разработ", "автоматиза", "workflow", "api"),
        "conflict": ("запрет", "риск", "ошибка", "скандал", "конкур", "vs", "против", "цены", "бесплат"),
        "fresh": ("запуст", "нов", "релиз", "обнов", "анонс", "preview", "beta"),
        "practical": ("как", "гайд", "пример", "практик", "сделать", "инструк", "шаблон"),
    }
    matched = {
        name: [word for word in words if word in text][:3]
        for name, words in hype_signals.items()
    }
    signal_count = sum(1 for words in matched.values() if words)
    viral_score = round(min(100, base_score * 0.55 + signal_count * 9 + evergreen_score(text) * 0.18), 1)
    angle = suggest_viral_angle(title, text, matched)
    evidence = build_viral_evidence(item, matched)
    return {
        "item_id": item["id"],
        "title": title,
        "source": item.get("source", ""),
        "url": item.get("url", ""),
        "viral_score": viral_score,
        "hype": min(10, max(1, int(round(viral_score / 10)))),
        "evergreen": min(10, max(1, int(round(evergreen_score(text) / 10)))),
        "angle": angle,
        "evidence": evidence,
        "weakness": suggest_weakness(text),
        "platform": suggest_platform(text),
    }


def evergreen_score(text: str) -> float:
    score = 35.0
    if any(word in text for word in ("как", "гайд", "инструк", "шаблон", "пример", "workflow")):
        score += 25
    if any(word in text for word in ("архитект", "база знаний", "агент", "автоматиза", "локальн")):
        score += 20
    if any(word in text for word in ("анонс", "релиз", "сегодня", "новост")):
        score -= 10
    return max(10, min(100, score))


def suggest_viral_angle(title: str, text: str, matched: dict[str, list[str]]) -> str:
    if matched.get("conflict"):
        return "Разбор конфликта: что здесь ломается, кто выиграет и что делать автору/разработчику."
    if matched.get("practical"):
        return "Практический заход: показать, как применить это в личном проекте или рабочем процессе."
    if matched.get("tools"):
        return "Инструментальный обзор: чем это полезно для вайбкодинга и автоматизации."
    if matched.get("fresh"):
        return "Новостной хук: быстро объяснить, почему обновление важно именно сейчас."
    if "ai" in text or "ии" in text:
        return "AI-ракурс: отделить реальную пользу от шума и дать личный вывод."
    return f"Авторский заход: взять тему «{title[:70]}» и раскрыть её через личный опыт."


def build_viral_evidence(item: dict, matched: dict[str, list[str]]) -> list[str]:
    evidence = [
        f"Источник: {item.get('source', 'неизвестно')}",
        f"Базовая оценка агента: {float(item.get('score') or 0):.1f}",
    ]
    for name, words in matched.items():
        if words:
            evidence.append(f"Сигнал {name}: {', '.join(words)}")
    return evidence[:5]


def suggest_weakness(text: str) -> str:
    if len(text) < 220:
        return "Мало контекста: перед публикацией нужно открыть источник и добрать факты."
    if any(word in text for word in ("анонс", "релиз", "нов")):
        return "Есть риск поверхностной новости: нужен личный вывод или практический сценарий."
    return "Проверить, не писали ли конкуренты то же самое, и найти незакрытый вопрос читателя."


def suggest_platform(text: str) -> str:
    if any(word in text for word in ("гайд", "инструк", "шаблон", "как")):
        return "blog"
    if any(word in text for word in ("архитект", "агент", "workflow", "база знаний")):
        return "wiki"
    if any(word in text for word in ("скандал", "риск", "против", "цены")):
        return "vc"
    return "telegram"


def get_search_schedule() -> tuple[int, int]:
    hour = int(agent.storage.get_setting("daily_run_hour", str(settings.daily_run_hour)) or 9)
    minute = int(agent.storage.get_setting("daily_run_minute", str(settings.daily_run_minute)) or 0)
    return hour, minute


def install_search_job() -> None:
    hour, minute = get_search_schedule()
    scheduler.add_job(
        scheduled_run,
        "cron",
        hour=hour,
        minute=minute,
        id="daily_collection",
        replace_existing=True,
    )


async def publish_due_publications() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    for queued in agent.storage.list_due_publications(now_iso):
        agent.storage.update_queue_status(queued["id"], "processing")
        try:
            if queued["platform"] in {"blog", "blog_project", "wiki"}:
                draft = agent.storage.get_draft(queued["draft_id"])
                if not draft:
                    raise PublishError("Черновик для отложенной публикации не найден")
                kind = {"blog": "article", "blog_project": "project", "wiki": "wiki"}[queued["platform"]]
                _, path = create_blog_post_from_draft(
                    queued["draft_id"],
                    draft,
                    queued["content"],
                    blog_kind=kind,
                )
                agent.storage.save_publication(
                    draft_id=queued["draft_id"],
                    item_id=queued["item_id"],
                    platform=queued["platform"],
                    status="published",
                    content=queued["content"],
                    response={"status": "published", "path": path},
                    image_path=queued.get("image_path"),
                )
                agent.storage.update_draft_status(queued["draft_id"], "published")
                agent.storage.update_queue_status(queued["id"], "published")
                continue
            result = await publish(
                queued["platform"],
                queued["content"],
                settings,
                image_path=queued.get("image_path"),
                overrides=publish_overrides(),
            )
            agent.storage.save_publication(
                draft_id=queued["draft_id"],
                item_id=queued["item_id"],
                platform=queued["platform"],
                status="published" if queued["platform"] in {"telegram", "max", "vk"} else "ready",
                content=queued["content"],
                response=result,
                image_path=queued.get("image_path"),
            )
            agent.storage.update_draft_status(queued["draft_id"], "published")
            agent.storage.update_queue_status(queued["id"], "published")
        except Exception as exc:  # noqa: BLE001 - background queue must keep working.
            agent.storage.update_queue_status(queued["id"], "failed", str(exc))


@asynccontextmanager
async def lifespan(_: FastAPI):
    agent.storage.interrupt_running_agent_runs(
        "editorial",
        "Редакционный прогон был прерван перезапуском сервиса. Запустите новый прогон.",
    )
    install_search_job()
    scheduler.add_job(
        publish_due_publications,
        "interval",
        minutes=1,
        id="publication_queue",
        replace_existing=True,
    )
    scheduler.add_job(
        telegram_control.poll_once,
        "interval",
        seconds=5,
        id="telegram_control",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Vibe Content Agent", lifespan=lifespan)
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

ZEN_VERIFICATION_TOKEN = settings.dzen_verification_token or ""
ZEN_VERIFICATION_PATH = (
    f"/zen_{ZEN_VERIFICATION_TOKEN}.html" if ZEN_VERIFICATION_TOKEN else "/zen-verification-disabled.html"
)
ZEN_VERIFICATION_PATH_LOWER = ZEN_VERIFICATION_PATH.lower()

PUBLIC_PREFIXES = ("/blog", "/projects", "/wiki", "/media")
PUBLIC_PATHS = {
    "/",
    "/health",
    "/robots.txt",
    "/sitemap.xml",
    "/llms.txt",
    "/llms-full.txt",
    "/indexnow-key.txt",
    "/rss.xml",
    "/feed.xml",
    ZEN_VERIFICATION_PATH,
    ZEN_VERIFICATION_PATH_LOWER,
}


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if is_public_request(request) or not settings.admin_password:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return auth_required()
    try:
        decoded = base64.b64decode(auth.removeprefix("Basic ").strip()).decode()
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return auth_required()
    if secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(
        password, settings.admin_password
    ):
        return await call_next(request)
    return auth_required()


def is_public_request(request: Request) -> bool:
    path = request.url.path
    if path in PUBLIC_PATHS:
        return True
    if path == "/blog/admin" or path.startswith("/blog/admin/"):
        return False
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in PUBLIC_PREFIXES)


def auth_required() -> Response:
    return PlainTextResponse(
        "Authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Vibe Content Agent"'},
    )


PLATFORM_LABELS = {
    "telegram": "Telegram",
    "max": "MAX",
    "vk": "ВКонтакте",
    "vc": "VC",
    "dzen": "Дзен",
    "blog": "Блог",
    "blog_project": "Проект",
    "wiki": "Wiki",
}

STATUS_LABELS = {
    "draft": "черновик",
    "new": "новое",
    "ready": "готово",
    "scheduled": "запланировано",
    "processing": "публикуется",
    "published": "опубликовано",
    "failed": "ошибка",
    "running": "в работе",
    "success": "успешно",
    "warning": "есть замечания",
    "open": "открыто",
    "waiting": "ожидает",
    "done": "готово",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


PUBLISH_SETTING_KEYS = [
    "telegram_bot_token",
    "telegram_channel_ids",
    "telegram_review_chat_id",
    "max_bot_token",
    "max_chat_ids",
    "vk_access_token",
    "vk_owner_id",
    "vc_api_token",
    "vc_workspace_id",
    "dzen_api_token",
    "dzen_publisher_id",
    "other_platforms",
]

AI_SETTING_KEYS = [
    "ai_text_provider",
    "openrouter_api_key",
    "openrouter_base_url",
    "openrouter_model",
    "gemini_api_key",
    "gemini_base_url",
    "gemini_model",
    "openai_api_key",
    "openai_model",
    "custom_text_api_key",
    "custom_text_base_url",
    "custom_text_model",
    "ai_image_provider",
    "openai_image_api_key",
    "openai_image_model",
    "openrouter_image_model",
    "openrouter_image_aspect_ratio",
    "openrouter_image_size",
    "cloudflare_image_worker_url",
    "cloudflare_image_api_key",
    "muapi_api_key",
    "muapi_base_url",
    "muapi_image_model",
    "muapi_image_aspect_ratio",
    "muapi_image_resolution",
    "muapi_video_model",
    "muapi_i2v_model",
    "muapi_video_aspect_ratio",
    "muapi_video_duration",
    "custom_image_notes",
]

SEO_SETTING_KEYS = [
    "site_base_url",
    "seo_site_name",
    "seo_default_description",
    "seo_analytics_script",
    "google_site_verification",
    "yandex_site_verification",
    "bing_site_verification",
    "indexnow_enabled",
    "indexnow_key",
]

APIFY_SETTING_KEYS = [
    "apify_api_token",
    "apify_enabled",
    "apify_timeout_seconds",
    "apify_max_items",
]


def publish_overrides() -> dict:
    values = agent.storage.get_settings_map(PUBLISH_SETTING_KEYS)
    telegram_channels = values.get("telegram_channel_ids") or settings.telegram_channel_id or ""
    return {
        "telegram_bot_token": values.get("telegram_bot_token") or settings.telegram_bot_token,
        "telegram_channel_ids": [
            item.strip()
            for item in telegram_channels.replace("\n", ",").split(",")
            if item.strip()
        ],
        "max_bot_token": values.get("max_bot_token"),
        "max_chat_ids": [
            item.strip()
            for item in (values.get("max_chat_ids") or "").replace("\n", ",").split(",")
            if item.strip()
        ],
        "vk_access_token": values.get("vk_access_token") or settings.vk_access_token,
        "vk_owner_id": values.get("vk_owner_id") or settings.vk_owner_id,
    }


def saved_setting(key: str, default: str = "") -> str:
    return agent.storage.get_setting(key, default) or default


def product_marketing_path() -> Path:
    return Path(".agents/product-marketing.md")


def read_product_marketing_context(limit: int = 1400) -> str:
    path = product_marketing_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:limit].strip()


def make_slug(title: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9а-яА-Я]+", "-", title.lower()).strip("-")[:70] or "post"
    slug = base
    index = 2
    while agent.storage.blog_slug_exists(slug):
        slug = f"{base}-{index}"
        index += 1
    return slug


def make_slug_for_post(title: str, post_id: int) -> str:
    base = re.sub(r"[^a-zA-Z0-9а-яА-Я]+", "-", title.lower()).strip("-")[:70] or "post"
    slug = base
    index = 2
    while agent.storage.blog_slug_exists_for_other_post(slug, post_id):
        slug = f"{base}-{index}"
        index += 1
    return slug


def excerpt_from_content(content: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", clean_article_text(content)).strip()
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def title_from_content(content: str) -> str:
    for line in clean_article_text(content).splitlines():
        stripped = line.strip(" #")
        if stripped:
            return stripped[:120]
    return "Новая публикация"


def blog_path_for_kind(kind: str, slug: str) -> str:
    if kind == "project":
        return f"/projects/{slug}"
    if kind == "wiki":
        return f"/wiki/{slug}"
    return f"/blog/{slug}"


DESTINATION_PLATFORMS = ("telegram", "max", "vk", "vc", "dzen", "blog", "blog_project", "wiki")
REACTIONS = {
    "useful": "Полезно",
    "sharp": "В точку",
    "more": "Хочу продолжение",
    "debate": "Спорно",
}


def destination_content(form_content: dict[str, str], destination: str, fallback: str) -> str:
    return clean_article_text(form_content.get(f"content_{destination}") or fallback)


def selected_destinations_from_form(form: object, destinations: list[str] | None, platform: str) -> list[str]:
    raw: list[str] = []
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        raw.extend(str(item) for item in getlist("destinations"))
    raw.extend(str(item) for item in (destinations or []))

    selected: list[str] = []
    for item in raw:
        if item in DESTINATION_PLATFORMS and item not in selected:
            selected.append(item)
    if not selected and platform in DESTINATION_PLATFORMS:
        selected = [platform]
    return selected


def create_blog_post_from_draft(
    draft_id: int,
    draft: dict,
    content: str,
    blog_kind: str = "article",
    demo_url: str = "",
    trial_limit: int = 5,
) -> tuple[int, str]:
    image = agent.storage.get_latest_media_asset(draft_id)
    clean_content = clean_article_text(content) or clean_article_text(draft["content"])
    title = title_from_content(clean_content)
    kind = blog_kind if blog_kind in {"article", "project", "wiki"} else "article"
    slug = make_slug(title)
    post_id = agent.storage.create_blog_post(
        title=title,
        slug=slug,
        kind=kind,
        excerpt=excerpt_from_content(clean_content),
        content=clean_content,
        cover_path=image["path"] if image else None,
        demo_url=demo_url.strip() or None,
        trial_limit=max(1, min(trial_limit, 20)),
        source_draft_id=draft_id,
    )
    if kind == "wiki":
        export_wiki_markdown(title, slug, clean_content, image["path"] if image else None, draft_id)
    path = blog_path_for_kind(kind, slug)
    schedule_indexnow(path)
    return post_id, path


def export_wiki_markdown(
    title: str,
    slug: str,
    content: str,
    cover_path: str | None = None,
    source_draft_id: int | None = None,
) -> Path:
    wiki_dir = Path(saved_setting("wiki_content_dir", "content/wiki"))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    path = wiki_dir / f"{slug}.md"
    frontmatter = [
        "---",
        f'title: "{escape_frontmatter(title)}"',
        f"slug: {slug}",
        "kind: wiki",
        f"date: {datetime.now(timezone.utc).date().isoformat()}",
        "tags:",
        "  - ai",
        "  - vibe-coding",
        "  - knowledge-base",
    ]
    if cover_path:
        frontmatter.append(f'cover: "{escape_frontmatter(cover_path)}"')
    if source_draft_id:
        frontmatter.append(f"source_draft_id: {source_draft_id}")
    frontmatter.extend(["---", "", clean_article_text(content).strip(), ""])
    path.write_text("\n".join(frontmatter), encoding="utf-8")
    return path


def escape_frontmatter(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_article_html(content: str) -> str:
    blocks: list[str] = []
    for raw in clean_article_text(content).split("\n\n"):
        block = raw.strip()
        if not block:
            continue
        if block.startswith("## "):
            blocks.append(f"<h2>{escape(block[3:].strip())}</h2>")
        elif block.startswith("# "):
            blocks.append(f"<h2>{escape(block[2:].strip())}</h2>")
        elif block.startswith(("- ", "* ")):
            items = [
                f"<li>{escape(line[2:].strip())}</li>"
                for line in block.splitlines()
                if line.startswith(("- ", "* "))
            ]
            blocks.append(f"<ul>{''.join(items)}</ul>")
        else:
            blocks.append(f"<p>{escape(block)}</p>")
    return "\n".join(blocks)


def site_base_url(request: Request | None = None) -> str:
    configured = saved_setting("site_base_url", "https://agent.gazon59.ru").strip()
    if configured:
        return configured.rstrip("/")
    if request:
        return str(request.base_url).rstrip("/")
    return "https://agent.gazon59.ru"


def absolute_site_url(path: str, request: Request | None = None) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{site_base_url(request)}/{path.lstrip('/')}"


def seo_site_name() -> str:
    return saved_setting("seo_site_name", "AI на миллион")


def seo_default_description() -> str:
    return saved_setting(
        "seo_default_description",
        "AI на миллион: статьи про искусственный интеллект, разработку, автоматизацию, AI-агентов и личные проекты в вайбкодинге.",
    )


def meta_description(text: str | None = None, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", clean_article_text(text or "")).strip()
    value = re.sub(r"(^|\s)[#*_`>-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        value = seo_default_description()
    return value[:limit].rstrip() + ("..." if len(value) > limit else "")


def json_ld_script(data: dict) -> str:
    return (
        '<script type="application/ld+json">'
        f"{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
        "</script>"
    )


def public_url_entries(request: Request | None = None) -> list[dict[str, str]]:
    entries = [
        {"loc": absolute_site_url("/", request), "lastmod": datetime.now(timezone.utc).date().isoformat(), "priority": "1.0"},
        {"loc": absolute_site_url("/blog", request), "lastmod": datetime.now(timezone.utc).date().isoformat(), "priority": "0.9"},
        {"loc": absolute_site_url("/projects", request), "lastmod": datetime.now(timezone.utc).date().isoformat(), "priority": "0.8"},
        {"loc": absolute_site_url("/wiki", request), "lastmod": datetime.now(timezone.utc).date().isoformat(), "priority": "0.7"},
    ]
    for post in agent.storage.list_blog_posts(limit=1000):
        entries.append(
            {
                "loc": absolute_site_url(blog_path_for_kind(post["kind"], post["slug"]), request),
                "lastmod": (post.get("updated_at") or post.get("created_at") or "").split(" ")[0]
                or datetime.now(timezone.utc).date().isoformat(),
                "priority": "0.8" if post["kind"] == "article" else "0.7",
            }
        )
    return entries


def organization_schema(request: Request | None = None) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": seo_site_name(),
        "url": absolute_site_url("/", request),
        "sameAs": ["https://t.me/AI_naMillion"],
    }


def breadcrumb_schema(items: list[tuple[str, str]], request: Request | None = None) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": name,
                "item": absolute_site_url(path, request),
            }
            for index, (name, path) in enumerate(items, start=1)
        ],
    }


def post_schema(post: dict, request: Request | None = None) -> dict:
    post_path = blog_path_for_kind(post["kind"], post["slug"])
    cover_src = media_url(post.get("cover_path"), settings)
    image_url = absolute_site_url(cover_src, request) if cover_src else None
    if post["kind"] == "project":
        data = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": post["title"],
            "description": meta_description(post.get("excerpt") or post.get("content")),
            "url": absolute_site_url(post_path, request),
            "applicationCategory": "AIApplication",
            "operatingSystem": "Web",
            "author": {"@type": "Organization", "name": seo_site_name()},
        }
    else:
        data = {
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post["title"],
            "description": meta_description(post.get("excerpt") or post.get("content")),
            "url": absolute_site_url(post_path, request),
            "datePublished": post.get("created_at"),
            "dateModified": post.get("updated_at") or post.get("created_at"),
            "inLanguage": "ru-RU",
            "author": {"@type": "Organization", "name": seo_site_name()},
            "publisher": organization_schema(request),
        }
    if image_url:
        data["image"] = [image_url]
    return data


def schedule_indexnow(path: str, request: Request | None = None) -> None:
    if saved_setting("indexnow_enabled", "on") == "off":
        return
    key = saved_setting("indexnow_key")
    if not key:
        return
    url = absolute_site_url(path, request)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(submit_indexnow([url], request))


async def submit_indexnow(urls: list[str], request: Request | None = None) -> dict:
    key = saved_setting("indexnow_key")
    if not key:
        return {"ok": False, "error": "IndexNow key не задан"}
    endpoint = "https://api.indexnow.org/indexnow"
    payload = {
        "host": site_base_url(request).replace("https://", "").replace("http://", "").split("/")[0],
        "key": key,
        "keyLocation": absolute_site_url("/indexnow-key.txt", request),
        "urlList": urls,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(endpoint, json=payload)
    return {"ok": response.status_code in {200, 202}, "status_code": response.status_code, "text": response.text[:500]}


def rss_date(value: str | None) -> str:
    parsed = datetime.now(timezone.utc)
    if value:
        try:
            parsed = date_parser.parse(value)
        except (ValueError, TypeError, OverflowError):
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_datetime(parsed.astimezone(timezone.utc), usegmt=True)


def cdata(value: str) -> str:
    return f"<![CDATA[{value.replace(']]>', ']]]]><![CDATA[>')}]]>"


def image_generation_config() -> dict:
    return agent.storage.get_settings_map(
        [
            "ai_image_provider",
            "openrouter_api_key",
            "openrouter_base_url",
            "openrouter_image_model",
            "openrouter_image_aspect_ratio",
            "openrouter_image_size",
            "cloudflare_image_worker_url",
            "cloudflare_image_api_key",
            "muapi_api_key",
            "muapi_base_url",
            "muapi_image_model",
            "muapi_image_aspect_ratio",
            "muapi_image_resolution",
            "muapi_video_model",
            "muapi_i2v_model",
            "muapi_video_aspect_ratio",
            "muapi_video_duration",
            "openai_image_api_key",
            "openai_image_model",
            "custom_image_notes",
        ]
    )


BASE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #f3f0ea;
  color: #171717;
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page { max-width: 1220px; margin: 0 auto; padding: 28px 28px 48px; }
.hero {
  background: linear-gradient(135deg, #111827 0%, #264653 52%, #2a9d8f 100%);
  color: #fff;
  border-radius: 8px;
  padding: 26px;
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: center;
  box-shadow: 0 18px 45px rgba(17, 24, 39, 0.18);
}
.hero h1 { margin: 0; font-size: 34px; letter-spacing: 0; }
.hero p { margin: 8px 0 0; color: #d7f5ef; }
.nav { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.btn, button, select, input[type="submit"] {
  background: #fff;
  color: #17202a;
  border: 1px solid #d7d0c4;
  border-radius: 8px;
  padding: 10px 13px;
  text-decoration: none;
  font-weight: 650;
  cursor: pointer;
}
.btn.primary, button.primary { background: #e76f51; border-color: #e76f51; color: #fff; }
.btn.dark { background: #17202a; border-color: #17202a; color: #fff; }
.panel {
  background: #fff;
  border: 1px solid #e0d8ca;
  border-radius: 8px;
  padding: 18px;
  margin-top: 18px;
  box-shadow: 0 10px 28px rgba(43, 34, 25, 0.06);
}
table { width: 100%; border-collapse: collapse; background: #fff; }
td, th { border-bottom: 1px solid #ebe4d8; padding: 13px; vertical-align: top; }
th { text-align: left; color: #6b6258; font-size: 13px; }
input, select, textarea {
  background: #fff;
  color: #171717;
  border: 1px solid #d7d0c4;
  border-radius: 8px;
  padding: 11px;
  max-width: 100%;
}
textarea { width: 100%; min-height: 560px; font-family: inherit; font-size: 16px; line-height: 1.55; }
label { display: block; font-weight: 700; margin: 10px 0 6px; }
a { color: #1d4ed8; }
.toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.toolbar input[type="search"] { min-width: min(520px, 100%); flex: 1; }
.grid-form { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)) auto; gap: 10px; align-items: end; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.pill { display: inline-block; border-radius: 999px; background: #edf7f5; color: #176b60; padding: 4px 9px; font-size: 12px; font-weight: 700; }
.search-widget { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.clock-progress {
  width: 108px;
  height: 108px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: conic-gradient(#e76f51 var(--progress), #eee6da 0);
  position: relative;
  box-shadow: inset 0 0 0 1px #e0d8ca;
}
.clock-progress::before {
  content: "";
  position: absolute;
  width: 78px;
  height: 78px;
  border-radius: 50%;
  background: #fff;
  box-shadow: inset 0 0 0 1px #e0d8ca;
}
.clock-progress::after {
  content: "";
  position: absolute;
  width: 3px;
  height: 34px;
  background: #17202a;
  border-radius: 999px;
  transform-origin: bottom center;
  transform: translateY(-17px) rotate(calc(var(--deg) * 1deg));
}
.clock-progress span { position: relative; z-index: 1; font-weight: 800; font-size: 18px; }
.progress-meta strong { display: block; font-size: 18px; margin-bottom: 4px; }
.progress-meta small { color: #6b6258; }
.settings-form input:not([type="submit"]), .settings-form select, .settings-form textarea {
  width: min(620px, 100%);
}
.settings-form h2 { margin-top: 28px; }
.settings-form h2:first-child { margin-top: 0; }
.settings-form .hint { color: #6b6258; max-width: 760px; line-height: 1.55; }
.blog-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.blog-card { border: 1px solid #e0d8ca; border-radius: 8px; padding: 16px; background: #fff; }
.blog-card h3 { margin: 0 0 8px; font-size: 20px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 14px 0; }
.stats div { background: #fff; border: 1px solid #e0d8ca; border-radius: 8px; padding: 14px; }
.stats strong { display: block; font-size: 28px; }
.stats span { color: #6b6258; }
.blog-cover { width: 100%; max-height: 380px; object-fit: cover; border-radius: 8px; border: 1px solid #e0d8ca; }
.article-body { max-width: 820px; font-size: 18px; line-height: 1.7; }
.article-body p { margin: 0 0 16px; }
.engagement { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(260px, 0.6fr); gap: 18px; align-items: start; }
.reaction-grid { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.reaction-grid button { min-width: 132px; text-align: left; }
.reaction-grid button.active { background: #17202a; border-color: #17202a; color: #fff; }
.share-grid { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.comment-list { display: grid; gap: 10px; margin-top: 14px; }
.comment-card { border: 1px solid #ebe4d8; border-radius: 8px; padding: 12px; background: #fffaf2; }
.comment-card strong { display: block; margin-bottom: 4px; }
.comment-card small { color: #6b6258; }
.comment-form textarea { min-height: 130px; }
.control-shell { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 18px; align-items: start; }
.control-rail {
  position: sticky;
  top: 18px;
  background: #17202a;
  color: #f8f4ec;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 18px 45px rgba(17, 24, 39, 0.18);
}
.control-rail h2 { margin: 0 0 10px; font-size: 20px; }
.control-rail p { color: #d7f5ef; line-height: 1.45; }
.control-rail a, .control-rail button {
  width: 100%;
  display: block;
  text-align: left;
  margin-top: 8px;
  background: rgba(255,255,255,0.08);
  border-color: rgba(255,255,255,0.16);
  color: #fff;
}
.control-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }
.control-card { grid-column: span 4; background: #fff; border: 1px solid #e0d8ca; border-radius: 8px; padding: 16px; box-shadow: 0 10px 28px rgba(43, 34, 25, 0.06); }
.control-card.wide { grid-column: span 8; }
.control-card.full { grid-column: 1 / -1; }
.control-card h2, .control-card h3 { margin-top: 0; }
.metric-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
.metric-tile { background: #f7f3ec; border: 1px solid #ebe4d8; border-radius: 8px; padding: 12px; }
.metric-tile strong { display: block; font-size: 26px; line-height: 1.1; }
.metric-tile span { color: #6b6258; font-size: 13px; }
.control-list { display: grid; gap: 10px; }
.control-list article { border: 1px solid #ebe4d8; border-radius: 8px; padding: 12px; background: #fffaf2; }
.control-list article h3 { font-size: 16px; margin: 0 0 6px; }
.control-list article p { margin: 0; color: #5f574f; line-height: 1.45; }
.status-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; background: #16a34a; }
.status-dot.warn { background: #f59e0b; }
.status-dot.off { background: #dc2626; }
.control-stage {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}
.control-stage .clock-progress { width: 86px; height: 86px; }
.control-stage .clock-progress::before { width: 62px; height: 62px; }
.control-stage .clock-progress::after { height: 28px; transform: translateY(-14px) rotate(calc(var(--deg) * 1deg)); }
.control-stage .clock-progress span { font-size: 15px; }
.control-stage strong { display: block; font-size: 18px; margin-bottom: 4px; }
.control-stage small { color: #6b6258; }
.control-stage + .control-stage { margin-top: 14px; padding-top: 14px; border-top: 1px solid #ebe4d8; }
.pipeline { display: flex; gap: 7px; flex-wrap: wrap; margin: 10px 0; }
.pipeline-step {
  border: 1px solid #e0d8ca;
  background: #f7f3ec;
  color: #6b6258;
  border-radius: 999px;
  padding: 5px 9px;
  font-size: 12px;
  font-weight: 800;
}
.pipeline-step.done { background: #edf7f5; color: #176b60; border-color: #b8ded6; }
.pipeline-step.active { background: #17202a; color: #fff; border-color: #17202a; }
.agent-role-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.agent-role { border: 1px solid #e0d8ca; border-radius: 8px; padding: 14px; background: #fff; }
.agent-role h3 { margin: 0 0 6px; font-size: 17px; }
.agent-role p { margin: 0; color: #5f574f; line-height: 1.45; }
.policy-strip { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
@media (max-width: 900px) {
  .page { padding: 18px; }
  .hero { display: block; }
  .nav { margin-top: 16px; }
  .grid-form { grid-template-columns: 1fr; }
  .engagement { grid-template-columns: 1fr; }
  .control-shell { grid-template-columns: 1fr; }
  .control-rail { position: static; }
  .control-card, .control-card.wide { grid-column: 1 / -1; }
}
"""


def page_shell(title: str, body: str, subtitle: str = "") -> str:
    subtitle_html = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    return f"""
    <!doctype html>
    <html lang="ru">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="alternate" type="application/rss+xml" title="AI на миллион — статьи" href="/rss.xml">
        <title>{escape(title)}</title>
        <style>{BASE_CSS}</style>
      </head>
      <body>
        <main class="page">
          <section class="hero">
            <div>
              <h1>{escape(title)}</h1>
              {subtitle_html}
            </div>
            <nav class="nav">
              <a class="btn dark" href="/admin/control">Центр</a>
              <a class="btn" href="/admin/topics">Темы</a>
              <a class="btn" href="/admin/sources">Источники</a>
              <a class="btn" href="/admin/apify/results">Apify</a>
              <a class="btn" href="/admin/osint">OSINT</a>
              <a class="btn" href="/admin/editorial">Редакция</a>
              <a class="btn" href="/admin/styles">Стили</a>
              <a class="btn" href="/admin/style-memory">Память стиля</a>
              <a class="btn" href="/admin/task-notes">Задачи</a>
              <a class="btn" href="/admin/model-cookbook">Cookbook</a>
              <a class="btn" href="/admin/schedule">Расписание</a>
              <a class="btn" href="/admin/settings">Настройки</a>
              <a class="btn" href="/admin/server">Сервер</a>
              <a class="btn" href="/admin/blog">Редактор блога</a>
              <a class="btn" href="/admin/media">Медиатека</a>
              <a class="btn" href="/admin/seo">SEO</a>
              <a class="btn" href="/admin/marketing">Маркетинг</a>
              <a class="btn" href="/admin/growth">Рост Telegram</a>
              <a class="btn" href="/">Сайт</a>
              <a class="btn" href="/blog">Статьи</a>
              <a class="btn" href="/projects">Проекты</a>
              <a class="btn" href="/wiki">Wiki</a>
              <a class="btn" href="/admin/publications">Журнал публикаций</a>
              <a class="btn" href="/admin/help">Помощь</a>
              <form method="post" action="/admin/run"><button class="primary" type="submit">Запустить поиск</button></form>
            </nav>
          </section>
          {body}
        </main>
      </body>
    </html>
    """


@app.get("/admin")
def admin_home() -> RedirectResponse:
    return RedirectResponse("/admin/control", status_code=303)


@app.get("/docs/telegram_growth_strategy.md", response_class=PlainTextResponse)
def telegram_growth_strategy_doc() -> str:
    path = Path("docs/telegram_growth_strategy.md")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Документ не найден")
    return path.read_text(encoding="utf-8")


@app.get("/docs/operator_help.md", response_class=PlainTextResponse)
def operator_help_doc() -> str:
    path = Path("docs/operator_help.md")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Документ не найден")
    return path.read_text(encoding="utf-8")


@app.get("/admin/help", response_class=HTMLResponse)
def admin_help_page(q: str = "") -> str:
    markdown = operator_help_doc()
    body = f"""
      <section class="panel">
        <div class="actions">
          <a class="btn" href="/docs/operator_help.md" target="_blank" rel="noopener">Открыть Markdown</a>
          <a class="btn" href="/docs/operator_help.md" download>Скачать</a>
        </div>
        <form class="toolbar" method="get" action="/admin/help">
          <input type="search" name="q" value="{escape(q)}" placeholder="Поиск по инструкции: AI Compare, Дзен, картинки, OSINT">
          <button type="submit">Найти</button>
          <a class="btn" href="/admin/help">Сбросить</a>
        </form>
        <p><small>Разделы раскрываются по клику. Это рабочая инструкция оператора: зависимости, ограничения и типовой сценарий.</small></p>
      </section>
      {render_operator_help(markdown, q.strip())}
    """
    return page_shell("Помощь", body, "Как работает агент, что на что влияет и где ограничения.")


def render_operator_help(markdown: str, query: str = "") -> str:
    sections: list[tuple[str, list[str]]] = []
    title = "Инструкция"
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if lines:
                sections.append((title, lines))
            title = line.replace("## ", "", 1).strip()
            lines = []
        elif not line.startswith("# "):
            lines.append(line)
    if lines:
        sections.append((title, lines))
    query_l = query.lower()
    rendered = []
    for section_title, section_lines in sections:
        text = "\n".join(section_lines).strip()
        haystack = f"{section_title}\n{text}".lower()
        if query_l and query_l not in haystack:
            continue
        rendered.append(render_help_section(section_title, text, open_section=bool(query_l)))
    if not rendered:
        rendered.append('<section class="panel"><p>По запросу ничего не найдено.</p></section>')
    return "\n".join(rendered)


def render_help_section(title: str, text: str, open_section: bool = False) -> str:
    html = render_help_markdown_fragment(text)
    open_attr = " open" if open_section or title == "Главная логика" else ""
    return f"""
      <section class="panel">
        <details{open_attr}>
          <summary><strong>{escape(title)}</strong></summary>
          <div class="help-content">{html}</div>
        </details>
      </section>
    """


def render_help_markdown_fragment(text: str) -> str:
    chunks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            chunks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush_list()
            continue
        if line.startswith("- "):
            list_items.append(f"<li>{inline_help_format(line[2:])}</li>")
            continue
        if re.match(r"^\d+\.\s+", line):
            flush_list()
            content = re.sub(r"^\d+\.\s+", "", line)
            chunks.append(f"<p>{inline_help_format(content)}</p>")
            continue
        flush_list()
        chunks.append(f"<p>{inline_help_format(line)}</p>")
    flush_list()
    return "\n".join(chunks)


def inline_help_format(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


@app.get("/admin/control", response_class=HTMLResponse)
def control_center() -> str:
    items = agent.storage.list_items(limit=6)
    drafts = agent.storage.list_drafts()[:6]
    publications = agent.storage.list_publications(limit=5)
    queue = agent.storage.list_publication_queue(limit=5)
    apify_items = agent.storage.list_apify_items(limit=5)
    sources = load_sources(settings.sources_path)
    source_count = len(sources)
    apify_source_count = len([source for source in sources if source.get("type") == "apify_actor"])
    settings_map = agent.storage.get_settings_map(
        [
            "telegram_bot_token",
            "telegram_channel_ids",
            "openrouter_api_key",
            "gemini_api_key",
            "apify_api_token",
            "cloudflare_image_worker_url",
            "cloudflare_image_api_key",
        ]
    )
    published_count = len(publications)
    scheduled_count = len([item for item in queue if item.get("status") == "scheduled"])
    draft_count = len(drafts)
    body = f"""
      <section class="control-shell">
        <aside class="control-rail">
          <h2>Пульт агента</h2>
          <p>Главный cockpit: поиск, идеи, публикации, источники и здоровье интеграций в одном месте.</p>
          <form id="controlSearchForm" method="post" action="/admin/run"><button class="primary" type="submit">Запустить поиск</button></form>
          <form id="controlViralForm" method="post" action="/admin/viral/start"><button type="submit">Найти вирусные темы</button></form>
          <form method="post" action="/admin/apify/run"><button type="submit">Собрать Apify</button></form>
          <a class="btn" href="/admin/topics">Темы</a>
          <a class="btn" href="/admin/marketing">Маркетинг</a>
          <a class="btn" href="/admin/settings">Настройки</a>
        </aside>
        <div class="control-grid">
          <section class="control-card full">
            <h2>Сводка</h2>
            <div class="metric-strip">
              {control_metric("Темы", len(items))}
              {control_metric("Черновики", draft_count)}
              {control_metric("Отложено", scheduled_count)}
              {control_metric("Публикации", published_count)}
              {control_metric("Источники", source_count)}
              {control_metric("Apify actors", apify_source_count)}
            </div>
          </section>
          <section class="control-card full">
            <h2>Статус задач</h2>
            <div class="control-stage">
              <div id="controlSearchClock" class="clock-progress" style="--progress: 0%; --deg: 0;"><span id="controlSearchProgress">0%</span></div>
              <div>
                <strong id="controlSearchStage">Поиск не запущен</strong>
                <small id="controlSearchDetails">Нажми «Запустить поиск», чтобы обновить темы.</small>
              </div>
            </div>
            <div class="control-stage">
              <div id="controlViralClock" class="clock-progress" style="--progress: 0%; --deg: 0;"><span id="controlViralProgress">0%</span></div>
              <div>
                <strong id="controlViralStage">Вирусный анализ не запущен</strong>
                <small id="controlViralDetails">Агент выделит темы, которые могут зайти аудитории.</small>
              </div>
            </div>
          </section>
          <section class="control-card wide">
            <h2>Свежие темы</h2>
            <div class="control-list">{''.join(render_control_item(item) for item in items) or '<p>Тем пока нет.</p>'}</div>
            <div class="actions"><a class="btn" href="/admin/topics">Все темы</a><a class="btn" href="/admin/apify/results">Выдача Apify</a></div>
          </section>
          <section class="control-card">
            <h2>Интеграции</h2>
            <div class="control-list">
              {integration_status("Telegram", bool(settings_map.get("telegram_bot_token") and settings_map.get("telegram_channel_ids")))}
              {integration_status("OpenRouter", bool(settings_map.get("openrouter_api_key")))}
              {integration_status("Gemini", bool(settings_map.get("gemini_api_key")))}
              {integration_status("Apify", bool(settings_map.get("apify_api_token")), note="лимит может быть исчерпан")}
              {integration_status("Cloudflare Images", bool(settings_map.get("cloudflare_image_worker_url") and settings_map.get("cloudflare_image_api_key")))}
            </div>
          </section>
          <section class="control-card">
            <h2>Редакционная команда</h2>
            <div class="control-list">
              <article>
                <h3>Harness-light</h3>
                <p>Поиск → отбор → фактчек → стиль → площадки → обложка → публикация → QA.</p>
                <div class="policy-strip">
                  <span class="pill">draft/commit</span>
                  <span class="pill">approval-gated</span>
                  <span class="pill">trace</span>
                </div>
              </article>
            </div>
            <div class="actions">
              <form method="post" action="/admin/editorial/run"><button type="submit">Запустить прогон</button></form>
              <a class="btn" href="/admin/editorial">Открыть редакцию</a>
            </div>
          </section>
          <section class="control-card">
            <h2>Черновики</h2>
            <div class="control-list">{''.join(render_control_draft(draft) for draft in drafts) or '<p>Черновиков пока нет.</p>'}</div>
            <div class="actions"><a class="btn" href="/admin/topics">Создать из темы</a></div>
          </section>
          <section class="control-card">
            <h2>Очередь</h2>
            <div class="control-list">{''.join(render_control_queue_item(item) for item in queue) or '<p>Отложенных публикаций нет.</p>'}</div>
            <div class="actions"><a class="btn" href="/admin/schedule">Расписание</a></div>
          </section>
          <section class="control-card">
            <h2>Apify</h2>
            <div class="control-list">{''.join(render_control_apify_item(item) for item in apify_items) or '<p>Кэш Apify пуст или лимит исчерпан.</p>'}</div>
            <div class="actions"><a class="btn" href="/admin/apify/results">Все Apify темы</a></div>
          </section>
          <section class="control-card wide">
            <h2>Последние размещения</h2>
            <div class="control-list">{''.join(render_control_publication(pub) for pub in publications) or '<p>Публикаций пока нет.</p>'}</div>
            <div class="actions"><a class="btn" href="/admin/publications">Журнал публикаций</a><a class="btn" href="/blog">Публичный блог</a></div>
          </section>
        </div>
      </section>
      <script>
        const searchForm = document.getElementById('controlSearchForm');
        const viralForm = document.getElementById('controlViralForm');

        function setClock(clock, label, progress) {{
          const value = Number(progress || 0);
          clock.style.setProperty('--progress', `${{value}}%`);
          clock.style.setProperty('--deg', String(value * 3.6));
          label.textContent = `${{value}}%`;
        }}

        async function pollControlSearch() {{
          const response = await fetch('/admin/run/status');
          const state = await response.json();
          setClock(document.getElementById('controlSearchClock'), document.getElementById('controlSearchProgress'), state.progress);
          document.getElementById('controlSearchStage').textContent = state.stage || 'ожидание';
          document.getElementById('controlSearchDetails').textContent = state.error
            ? `Ошибка: ${{state.error}}`
            : `Найдено: ${{state.fetched || 0}} · новых: ${{state.inserted || 0}}`;
          if (searchForm) {{
            const button = searchForm.querySelector('button');
            button.disabled = Boolean(state.running);
            button.textContent = state.running ? 'Ищу...' : 'Запустить поиск';
          }}
          if (state.running) setTimeout(pollControlSearch, 900);
        }}

        async function pollControlViral() {{
          const response = await fetch('/admin/viral/status');
          const state = await response.json();
          setClock(document.getElementById('controlViralClock'), document.getElementById('controlViralProgress'), state.progress);
          document.getElementById('controlViralStage').textContent = state.stage || 'ожидание';
          document.getElementById('controlViralDetails').textContent = state.error
            ? `Ошибка: ${{state.error}}`
            : `Идей: ${{(state.ideas || []).length}}`;
          if (viralForm) {{
            const button = viralForm.querySelector('button');
            button.disabled = Boolean(state.running);
            button.textContent = state.running ? 'Анализирую...' : 'Найти вирусные темы';
          }}
          if (state.running) setTimeout(pollControlViral, 900);
        }}

        if (searchForm) {{
          searchForm.addEventListener('submit', async (event) => {{
            event.preventDefault();
            await fetch('/admin/run/start', {{ method: 'POST' }});
            pollControlSearch();
          }});
        }}
        if (viralForm) {{
          viralForm.addEventListener('submit', async (event) => {{
            event.preventDefault();
            await fetch('/admin/viral/start', {{ method: 'POST' }});
            pollControlViral();
          }});
        }}
        pollControlSearch();
        pollControlViral();
      </script>
    """
    return page_shell("Центр управления", body, "Единый пульт поиска, источников, публикаций и здоровья интеграций.")


def control_metric(label: str, value: int | str) -> str:
    return f'<div class="metric-tile"><strong>{escape(str(value))}</strong><span>{escape(label)}</span></div>'


def integration_status(label: str, enabled: bool, note: str = "") -> str:
    cls = "status-dot" if enabled else "status-dot off"
    state = "настроено" if enabled else "не настроено"
    note_html = f"<br><small>{escape(note)}</small>" if note else ""
    return f'<article><h3><span class="{cls}"></span>{escape(label)}</h3><p>{state}{note_html}</p></article>'


def render_control_item(item: dict) -> str:
    apify = '<span class="pill">Apify</span> ' if str(item.get("source") or "").startswith("Apify:") else ""
    return f"""
      <article>
        <h3>{apify}{escape(item.get('title') or '')}</h3>
        <p>{escape(item.get('source') or '')} · оценка {float(item.get('score') or 0):.2f}</p>
        <div class="actions">
          <a class="btn" href="{escape(item.get('url') or '#')}" target="_blank" rel="noopener">Источник</a>
          <form method="post" action="/items/{item['id']}/draft"><input type="hidden" name="platform" value="blog"><button type="submit">Черновик</button></form>
        </div>
      </article>
    """


def render_control_draft(draft: dict) -> str:
    return f"""
      <article>
        <h3>Черновик #{draft['id']}</h3>
        <p>{escape(platform_label(draft.get('platform') or ''))} · {escape(status_label(draft.get('status') or 'draft'))}</p>
        <div class="actions"><a class="btn" href="/drafts/{draft['id']}">Открыть</a></div>
      </article>
    """


def render_control_queue_item(item: dict) -> str:
    return f"""
      <article>
        <h3>{escape(platform_label(item.get('platform') or ''))}</h3>
        <p>{escape(item.get('item_title') or '')}<br><small>{escape(item.get('scheduled_at') or '')} · {escape(status_label(item.get('status') or 'scheduled'))}</small></p>
      </article>
    """


def render_control_apify_item(item: dict) -> str:
    item_id = item.get("item_id")
    draft_link = (
        f'<form method="post" action="/items/{item_id}/draft"><input type="hidden" name="platform" value="blog"><button type="submit">Черновик</button></form>'
        if item_id
        else ""
    )
    return f"""
      <article>
        <h3>{escape(item.get('title') or '')}</h3>
        <p>{escape(item.get('source') or '')}<br><small>{escape(item.get('actor_id') or '')}</small></p>
        <div class="actions"><a class="btn" href="{escape(item.get('url') or '#')}" target="_blank" rel="noopener">Источник</a>{draft_link}</div>
      </article>
    """


def render_control_publication(pub: dict) -> str:
    return f"""
      <article>
        <h3>{escape(pub.get('item_title') or '')}</h3>
        <p>{escape(platform_label(pub.get('platform') or ''))} · {escape(status_label(pub.get('status') or ''))}<br><small>{escape(pub.get('published_at') or '')}</small></p>
      </article>
    """


EDITORIAL_ROLES = [
    (
        "Scout",
        "Собирает темы из RSS, Telegram, Apify, GitHub и сайтов. Только read/search.",
        "search_only",
    ),
    (
        "Trend Analyst",
        "Оценивает вирусность, свежесть, боль аудитории и потенциал поста.",
        "compute_only",
    ),
    (
        "Fact Checker",
        "Проверяет ссылки, даты, первоисточники и помечает слабые места.",
        "read_only",
    ),
    (
        "Style Editor",
        "Переписывает в твоём стиле без служебного мусора и дублей.",
        "draft_only",
    ),
    (
        "Platform Editor",
        "Делает версии под блог, Telegram, VK, VC и Дзен.",
        "draft_only",
    ),
    (
        "Image Director",
        "Готовит промпт, обложку и проверяет, что картинка подходит теме.",
        "draft_only",
    ),
    (
        "Publisher",
        "Публикует только выбранные площадки; внешние отправки проходят через runtime.",
        "write_external",
    ),
    (
        "QA",
        "Финальная проверка: источник, формат, ссылка, обложка, журнал публикаций.",
        "verification",
    ),
]

EDITORIAL_PHASES = [
    ("found", "Найдено"),
    ("selected", "Отобрано"),
    ("draft", "Черновик"),
    ("style", "Стиль"),
    ("media", "Обложка"),
    ("scheduled", "Отложено"),
    ("published", "Опубликовано"),
    ("qa", "QA"),
]


@app.get("/admin/editorial", response_class=HTMLResponse)
def editorial_team_page() -> str:
    drafts = agent.storage.list_drafts()[:18]
    runs = agent.storage.list_agent_runs(limit=8)
    draft_cards = "\n".join(render_editorial_draft_card(draft) for draft in drafts)
    run_cards = "\n".join(render_editorial_run_card(run) for run in runs)
    roles = "\n".join(render_editorial_role(name, purpose, risk) for name, purpose, risk in EDITORIAL_ROLES)
    body = f"""
      <section class="panel">
        <h2>Редакционная команда AI на миллион</h2>
        <p><small>Версия Harness-light: модель предлагает, приложение валидирует, выполняет и пишет результат в журнал. Рискованные действия разделены на draft и commit.</small></p>
        <div class="actions">
          <form method="post" action="/admin/editorial/run"><button class="primary" type="submit">Запустить редакционный прогон</button></form>
          <a class="btn" href="/admin/control">Центр управления</a>
        </div>
        <div class="agent-role-grid">{roles}</div>
      </section>
      <section class="panel">
        <h2>Журнал прогонов</h2>
        <div class="control-list">{run_cards or '<p>Прогонов пока нет.</p>'}</div>
      </section>
      <section class="panel">
        <h2>Runtime-правила</h2>
        <div class="blog-grid">
          <article class="blog-card"><h3>Draft / commit</h3><p>Рерайт, версии и картинки можно делать как черновики. Внешняя публикация идёт только через явную кнопку и выбранные площадки.</p></article>
          <article class="blog-card"><h3>Узкие инструменты</h3><p>Не “отправь куда-нибудь”, а конкретные действия: собрать Apify, создать черновик, отправить Telegram, создать блог-пост.</p></article>
          <article class="blog-card"><h3>Наблюдения</h3><p>Ошибки, таймауты, лимиты Apify/OpenRouter и результат публикации должны быть видны в интерфейсе и журнале.</p></article>
        </div>
      </section>
      <section class="panel">
        <h2>Pipeline последних материалов</h2>
        <div class="control-list">{draft_cards or '<p>Черновиков пока нет.</p>'}</div>
      </section>
    """
    return page_shell("Редакционная команда", body, "Роли, правила и статус материалов по Harness-light.")


@app.post("/admin/editorial/run")
async def run_editorial_pipeline() -> RedirectResponse:
    running = agent.storage.get_latest_running_agent_run("editorial")
    if running:
        return RedirectResponse(f"/admin/editorial/runs/{running['id']}", status_code=303)
    run_id = agent.storage.create_agent_run(
        kind="editorial",
        objective="Найти сильную тему, проверить источник и подготовить blog-черновик без внешней публикации.",
    )
    asyncio.create_task(execute_editorial_pipeline(run_id))
    return RedirectResponse(f"/admin/editorial/runs/{run_id}", status_code=303)


async def execute_editorial_pipeline(run_id: int) -> None:
    selected_item: dict | None = None
    draft_id: int | None = None
    warnings: list[str] = []
    try:
        scout_step = agent.storage.create_agent_step(
            run_id,
            "Scout",
            "search_only",
            "Собираю свежие темы из настроенных источников.",
        )
        try:
            result = await agent.collect_and_rank()
            agent.storage.finish_agent_step(
                scout_step,
                "success",
                f"Сбор завершён: найдено {result.get('fetched', 0)}, новых {result.get('inserted', 0)}.",
                result,
            )
        except Exception as exc:  # noqa: BLE001 - trace should capture collection failures.
            warnings.append(f"Scout: {exc}")
            agent.storage.finish_agent_step(
                scout_step,
                "warning",
                "Сбор источников дал ошибку, продолжаю по уже сохранённым темам.",
                {"error": str(exc)},
            )

        trend_step = agent.storage.create_agent_step(
            run_id,
            "Trend Analyst",
            "compute_only",
            "Выбираю лучшую новую тему по оценке и пригодности для блога.",
        )
        candidates = agent.storage.list_editorial_candidates(limit=40)
        if not candidates:
            agent.storage.finish_agent_step(
                trend_step,
                "failed",
                "Нет новых подходящих тем: сильные темы уже были взяты в черновики или публикации.",
                {"candidate_count": 0},
            )
            agent.storage.finish_agent_run(
                run_id,
                "failed",
                "Редакционный прогон остановлен: нет новых подходящих тем.",
                item_id=None,
            )
            return
        selected_item = candidates[0]
        agent.storage.finish_agent_step(
            trend_step,
            "success",
            f"Выбрана тема: {selected_item['title']}",
            {
                "item_id": selected_item["id"],
                "score": selected_item.get("score"),
                "source": selected_item.get("source"),
                "url": selected_item.get("url"),
                "candidate_count": len(candidates),
                "dedupe_rule": "items with drafts, publications or completed editorial runs are skipped",
            },
        )

        fact_step = agent.storage.create_agent_step(
            run_id,
            "Fact Checker",
            "read_only",
            "Проверяю, что у темы есть открываемый источник и нормальное описание.",
        )
        fact_warnings = []
        if not str(selected_item.get("url") or "").startswith(("http://", "https://")):
            fact_warnings.append("У источника нет обычного HTTP URL.")
        if len(selected_item.get("summary") or "") < 40:
            fact_warnings.append("Описание короткое, понадобится ручная проверка.")
        agent.storage.finish_agent_step(
            fact_step,
            "warning" if fact_warnings else "success",
            "Источник проверен." if not fact_warnings else "Источник требует внимания.",
            {"warnings": fact_warnings, "url": selected_item.get("url")},
        )
        warnings.extend(f"Fact Checker: {item}" for item in fact_warnings)

        style_step = agent.storage.create_agent_step(
            run_id,
            "Style Editor",
            "draft_only",
            "Создаю черновик статьи для нашего блога.",
        )
        try:
            draft = await asyncio.wait_for(agent.draft(selected_item["id"], "blog"), timeout=120)
            draft_id = int(draft["draft_id"])
            agent.storage.finish_agent_step(
                style_step,
                "success",
                f"AI-черновик создан: #{draft_id}.",
                {"draft_id": draft_id, "provider": "configured_ai"},
            )
        except Exception as exc:  # noqa: BLE001 - fallback draft keeps the workflow testable.
            fallback = fallback_editorial_draft(selected_item)
            draft_id = agent.storage.save_draft(selected_item["id"], "blog", fallback)
            warnings.append(f"Style Editor fallback: {exc}")
            agent.storage.finish_agent_step(
                style_step,
                "warning",
                f"AI-рерайт не сработал, создан fallback-черновик #{draft_id}.",
                {"draft_id": draft_id, "error": str(exc)},
            )

        platform_step = agent.storage.create_agent_step(
            run_id,
            "Platform Editor",
            "draft_only",
            "Фиксирую безопасные следующие площадки без автоматической отправки.",
        )
        agent.storage.finish_agent_step(
            platform_step,
            "success",
            "Материал готов к ручной адаптации под блог, Telegram, VK, VC и Дзен.",
            {"allowed_next_actions": ["rewrite", "generate_image", "variants", "publish_selected"]},
        )

        qa_step = agent.storage.create_agent_step(
            run_id,
            "QA",
            "verification",
            "Проверяю минимальные условия готовности черновика.",
        )
        draft = agent.storage.get_draft(draft_id)
        qa_warnings = []
        if not draft or len(draft.get("content") or "") < 200:
            qa_warnings.append("Черновик короткий, нужен рерайт.")
        if warnings:
            qa_warnings.append("Есть предупреждения в предыдущих шагах.")
        agent.storage.finish_agent_step(
            qa_step,
            "warning" if qa_warnings else "success",
            "QA завершён." if not qa_warnings else "QA завершён с замечаниями.",
            {"warnings": qa_warnings, "draft_id": draft_id},
        )

        status = "warning" if warnings or qa_warnings else "success"
        summary = f"Редакционный прогон завершён: черновик #{draft_id}."
        agent.storage.finish_agent_run(
            run_id,
            status,
            summary,
            error="; ".join(warnings[:4]),
            item_id=selected_item["id"],
            draft_id=draft_id,
        )
    except Exception as exc:  # noqa: BLE001 - runtime trace must record unexpected failures.
        agent.storage.finish_agent_run(
            run_id,
            "failed",
            "Редакционный прогон упал на runtime-ошибке.",
            error=str(exc),
            item_id=selected_item["id"] if selected_item else None,
            draft_id=draft_id,
        )


@app.get("/admin/editorial/runs/{run_id}", response_class=HTMLResponse)
def view_editorial_run(run_id: int) -> str:
    return editorial_run_result(run_id)


def editorial_run_result(run_id: int) -> str:
    run = agent.storage.get_agent_run(run_id) or {"id": run_id, "status": "unknown", "summary": ""}
    steps = agent.storage.list_agent_steps(run_id)
    is_running = run.get("status") == "running"
    refresh_script = "<script>setTimeout(() => location.reload(), 2500);</script>" if is_running else ""
    running_hint = (
        "<p><small>Прогон выполняется в фоне. Страница обновится автоматически, Cloudflare больше не должен обрывать запрос.</small></p>"
        if is_running
        else ""
    )
    body = f"""
      <section class="panel">
        <h2>Редакционный прогон #{run_id}</h2>
        <p><span class="pill">{escape(status_label(run.get('status') or ''))}</span></p>
        <p>{escape(run.get('summary') or run.get('objective') or '')}</p>
        {running_hint}
        {f'<p><strong>Замечания:</strong> {escape(run.get("error") or "")}</p>' if run.get("error") else ''}
        <div class="control-list">{''.join(render_editorial_step(step) for step in steps) or '<p>Первые шаги ещё не записаны.</p>'}</div>
        <div class="actions">
          {f'<a class="btn primary" href="/drafts/{run["draft_id"]}">Открыть черновик #{run["draft_id"]}</a>' if run.get("draft_id") else ''}
          <a class="btn" href="/admin/editorial">Вернуться в редакцию</a>
          <a class="btn" href="/admin/control">Центр управления</a>
        </div>
      </section>
      {refresh_script}
    """
    return page_shell("Редакционный прогон", body, "Trace каждого шага: роль, риск, наблюдение, статус.")


def render_editorial_run_card(run: dict) -> str:
    steps = agent.storage.list_agent_steps(run["id"])
    return f"""
      <article>
        <h3>Прогон #{run['id']} · {escape(status_label(run.get('status') or ''))}</h3>
        <p>{escape(run.get('summary') or run.get('objective') or '')}</p>
        <p><small>{escape(run.get('started_at') or '')} · шагов: {len(steps)} · тема: {escape(run.get('item_title') or '—')}</small></p>
        {render_run_pipeline(steps)}
        <div class="actions">
          {f'<a class="btn" href="/drafts/{run["draft_id"]}">Черновик #{run["draft_id"]}</a>' if run.get("draft_id") else ''}
        </div>
      </article>
    """


def render_editorial_step(step: dict) -> str:
    return f"""
      <article>
        <h3>{escape(step.get('role') or '')} · {escape(status_label(step.get('status') or ''))}</h3>
        <p><span class="pill">{escape(step.get('risk_class') or '')}</span></p>
        <p>{escape(step.get('summary') or '')}</p>
      </article>
    """


def render_run_pipeline(steps: list[dict]) -> str:
    if not steps:
        return ""
    rendered = []
    for step in steps:
        status = step.get("status")
        cls = "pipeline-step"
        if status == "success":
            cls += " done"
        elif status == "running":
            cls += " active"
        elif status in {"warning", "failed"}:
            cls += " active"
        rendered.append(f'<span class="{cls}">{escape(step.get("role") or "")}</span>')
    return f'<div class="pipeline">{"".join(rendered)}</div>'


def fallback_editorial_draft(item: dict) -> str:
    title = item.get("title") or "Новая AI-тема"
    summary = item.get("summary") or "Короткое описание пока не найдено."
    url = item.get("url") or ""
    return clean_article_text(
        f"""
        {title}

        Нашёл тему, которую стоит разобрать для канала и блога.

        Что известно сейчас:
        {summary}

        Почему это может зайти:
        - тема связана с ИИ, разработкой или инструментами для автоматизации;
        - у неё есть практический угол для предпринимателей и разработчиков;
        - из неё можно сделать пост с личным выводом и пользой для аудитории.

        Что проверить перед публикацией:
        - первоисточник и дату;
        - есть ли рабочий пример или демо;
        - какую пользу это даёт нашим проектам в вайбкодинге.

        Источник: {url}
        """
    )


def render_editorial_role(name: str, purpose: str, risk: str) -> str:
    return f"""
      <article class="agent-role">
        <h3>{escape(name)}</h3>
        <p>{escape(purpose)}</p>
        <p><span class="pill">{escape(risk)}</span></p>
      </article>
    """


def render_editorial_draft_card(draft: dict) -> str:
    item = agent.storage.get_item(draft["item_id"]) or {}
    current_phase = editorial_phase_for_draft(draft)
    title = item.get("title") or f"Черновик #{draft['id']}"
    next_action = editorial_next_action(draft, current_phase)
    return f"""
      <article>
        <h3>{escape(title)}</h3>
        <p>{escape(platform_label(draft.get('platform') or ''))} · {escape(status_label(draft.get('status') or 'draft'))}</p>
        {render_editorial_pipeline(current_phase)}
        <p><strong>Следующий безопасный шаг:</strong> {escape(next_action)}</p>
        <div class="actions">
          <a class="btn" href="/drafts/{draft['id']}">Открыть</a>
          <a class="btn" href="{escape(item.get('url') or '#')}" target="_blank" rel="noopener">Источник</a>
        </div>
      </article>
    """


def editorial_phase_for_draft(draft: dict) -> str:
    status = draft.get("status") or "draft"
    if status == "published":
        return "qa"
    if status == "scheduled":
        return "scheduled"
    if status == "ready":
        return "style"
    return "draft"


def editorial_next_action(draft: dict, phase: str) -> str:
    platform = draft.get("platform") or ""
    if phase == "qa":
        return "Проверить журнал, ссылку и реакцию аудитории."
    if phase == "scheduled":
        return "Дождаться публикации или поправить расписание."
    if phase == "style":
        return "Выбрать площадки и отправить через явную кнопку публикации."
    if platform in {"telegram", "max", "vk", "vc", "dzen", "blog"}:
        return "Сделать рерайт в стиле, добавить обложку и подготовить версии под площадки."
    return "Выбрать целевую площадку и создать версию материала."


def render_editorial_pipeline(current_phase: str) -> str:
    active_index = next(
        (index for index, (phase, _) in enumerate(EDITORIAL_PHASES) if phase == current_phase),
        0,
    )
    steps = []
    for index, (phase, label) in enumerate(EDITORIAL_PHASES):
        cls = "pipeline-step"
        if index < active_index:
            cls += " done"
        elif phase == current_phase:
            cls += " active"
        steps.append(f'<span class="{cls}">{escape(label)}</span>')
    return f'<div class="pipeline">{"".join(steps)}</div>'


def public_shell(
    title: str,
    body: str,
    subtitle: str = "",
    *,
    request: Request | None = None,
    path: str = "",
    description: str = "",
    image_url: str = "",
    schema: list[dict] | dict | None = None,
) -> str:
    subtitle_html = f"<p>{escape(subtitle)}</p>" if subtitle else ""
    desc = meta_description(description or subtitle)
    canonical = absolute_site_url(path or "/", request)
    full_title = title if title == seo_site_name() else f"{title} — {seo_site_name()}"
    image_meta = f'<meta property="og:image" content="{escape(image_url)}">' if image_url else ""
    verification_meta = "\n".join(
        item
        for item in [
            f'<meta name="google-site-verification" content="{escape(saved_setting("google_site_verification"))}">'
            if saved_setting("google_site_verification")
            else "",
            f'<meta name="yandex-verification" content="{escape(saved_setting("yandex_site_verification"))}">'
            if saved_setting("yandex_site_verification")
            else "",
            f'<meta name="msvalidate.01" content="{escape(saved_setting("bing_site_verification"))}">'
            if saved_setting("bing_site_verification")
            else "",
        ]
        if item
    )
    schema_items: list[dict] = []
    if schema:
        schema_items = schema if isinstance(schema, list) else [schema]
    schema_html = "\n".join(json_ld_script(item) for item in schema_items)
    analytics_script = saved_setting("seo_analytics_script")
    return f"""
    <!doctype html>
    <html lang="ru">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta name="description" content="{escape(desc)}">
        <link rel="canonical" href="{escape(canonical)}">
        <link rel="alternate" type="application/rss+xml" title="AI на миллион — статьи" href="/rss.xml">
        <meta property="og:type" content="website">
        <meta property="og:site_name" content="{escape(seo_site_name())}">
        <meta property="og:title" content="{escape(full_title)}">
        <meta property="og:description" content="{escape(desc)}">
        <meta property="og:url" content="{escape(canonical)}">
        {image_meta}
        <meta name="twitter:card" content="{'summary_large_image' if image_url else 'summary'}">
        <meta name="twitter:title" content="{escape(full_title)}">
        <meta name="twitter:description" content="{escape(desc)}">
        {verification_meta}
        {schema_html}
        <title>{escape(full_title)}</title>
        <style>{BASE_CSS}</style>
        {analytics_script}
      </head>
      <body>
        <main class="page">
          <section class="hero">
            <div>
              <h1>{escape(title)}</h1>
              {subtitle_html}
            </div>
            <nav class="nav">
              <a class="btn" href="/">Главная</a>
              <a class="btn" href="/blog">Статьи</a>
              <a class="btn" href="/projects">Проекты</a>
              <a class="btn" href="/wiki">Wiki</a>
              <a class="btn" href="https://t.me/AI_naMillion" target="_blank" rel="noopener">Telegram</a>
            </nav>
          </section>
          {body}
        </main>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get(ZEN_VERIFICATION_PATH, response_class=HTMLResponse)
@app.get(ZEN_VERIFICATION_PATH_LOWER, response_class=HTMLResponse)
def zen_verification() -> str:
    if not ZEN_VERIFICATION_TOKEN:
        return '<meta name="zen-verification" content="" />'
    return f'<meta name="zen-verification" content="{ZEN_VERIFICATION_TOKEN}" />'


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request) -> str:
    base = site_base_url(request)
    return "\n".join(
        [
            "User-agent: *",
            "Disallow: /admin/",
            "Disallow: /settings",
            "Disallow: /drafts/",
            "Disallow: /items/",
            "Disallow: /run",
            "Allow: /media/",
            f"Sitemap: {base}/sitemap.xml",
            f"Sitemap: {base}/rss.xml",
            "",
        ]
    )


@app.get("/sitemap.xml")
def sitemap_xml(request: Request) -> Response:
    urls = "\n".join(
        f"""
  <url>
    <loc>{escape(entry['loc'])}</loc>
    <lastmod>{escape(entry['lastmod'])}</lastmod>
    <changefreq>{'daily' if entry['loc'].endswith(('/blog', '/projects', '/wiki')) else 'weekly'}</changefreq>
    <priority>{escape(entry['priority'])}</priority>
  </url>"""
        for entry in public_url_entries(request)
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""
    return Response(xml, media_type="application/xml; charset=utf-8")


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt(request: Request) -> str:
    lines = [
        f"# {seo_site_name()}",
        "",
        seo_default_description(),
        "",
        "## Основные разделы",
        f"- [Статьи]({absolute_site_url('/blog', request)}): материалы про ИИ, разработку, автоматизацию и вайбкодинг.",
        f"- [Проекты]({absolute_site_url('/projects', request)}): AI-инструменты и эксперименты, которые можно попробовать.",
        f"- [Wiki]({absolute_site_url('/wiki', request)}): база знаний по AI-проектам и рабочим практикам.",
        "",
        "## Последние материалы",
    ]
    for post in agent.storage.list_blog_posts(limit=100):
        path = blog_path_for_kind(post["kind"], post["slug"])
        lines.append(
            f"- [{post['title']}]({absolute_site_url(path, request)}): {meta_description(post.get('excerpt') or post.get('content'), 240)}"
        )
    lines.append("")
    return "\n".join(lines)


@app.get("/llms-full.txt", response_class=PlainTextResponse)
def llms_full_txt(request: Request) -> str:
    sections = [llms_txt(request), "\n## Полные тексты\n"]
    for post in agent.storage.list_blog_posts(limit=100):
        path = blog_path_for_kind(post["kind"], post["slug"])
        sections.append(f"\n### {post['title']}\n")
        sections.append(f"URL: {absolute_site_url(path, request)}\n")
        sections.append(clean_article_text(post["content"]).strip())
        sections.append("\n")
    return "\n".join(sections)


@app.get("/indexnow-key.txt", response_class=PlainTextResponse)
def indexnow_key_file() -> str:
    key = saved_setting("indexnow_key")
    if not key:
        raise HTTPException(status_code=404, detail="IndexNow key не задан")
    return key


@app.get("/rss.xml")
@app.get("/feed.xml")
@app.head("/rss.xml")
@app.head("/feed.xml")
def rss_feed(request: Request) -> Response:
    posts = agent.storage.list_blog_posts(kind="article", limit=100)
    last_build = rss_date(posts[0]["updated_at"] if posts else None)
    channel_link = absolute_site_url("/", request)
    items: list[str] = []
    for post in posts:
        post_link = absolute_site_url(f"/blog/{post['slug']}", request)
        cover_src = media_url(post.get("cover_path"), settings)
        cover_url = absolute_site_url(cover_src, request) if cover_src else ""
        html_content = render_article_html(post["content"])
        if cover_url:
            html_content = (
                f'<p><img src="{escape(cover_url)}" alt="{escape(post["title"])}"></p>\n'
                f"{html_content}"
            )
        media = (
            f'<media:content url="{escape(cover_url)}" medium="image" />'
            if cover_url
            else ""
        )
        items.append(
            f"""
    <item>
      <title>{escape(post["title"])}</title>
      <link>{escape(post_link)}</link>
      <guid isPermaLink="true">{escape(post_link)}</guid>
      <pubDate>{rss_date(post.get("created_at"))}</pubDate>
      <description>{cdata(post.get("excerpt") or excerpt_from_content(post["content"]))}</description>
      <content:encoded>{cdata(html_content)}</content:encoded>
      {media}
    </item>"""
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>AI на миллион — AI, разработка и вайбкодинг</title>
    <link>{escape(channel_link)}</link>
    <description>Статьи про ИИ, разработку, автоматизацию и личные проекты в вайбкодинге.</description>
    <language>ru</language>
    <lastBuildDate>{last_build}</lastBuildDate>
    <ttl>60</ttl>
{''.join(items)}
  </channel>
</rss>
"""
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@app.post("/admin/run")
@app.post("/run")
async def run_collection() -> RedirectResponse:
    await run_search_with_progress()
    return RedirectResponse("/admin/control", status_code=303)


@app.post("/admin/run/start")
@app.post("/run/start")
async def start_collection() -> dict:
    if not search_state["running"]:
        asyncio.create_task(run_search_with_progress())
    return search_state


@app.get("/admin/run/status")
@app.get("/run/status")
def run_status() -> dict:
    return search_state


@app.post("/admin/viral/start")
@app.post("/viral/start")
async def start_viral_research() -> dict:
    if not viral_state["running"]:
        asyncio.create_task(run_viral_research_with_progress())
    return viral_state


@app.get("/admin/viral/status")
@app.get("/viral/status")
def viral_status() -> dict:
    return viral_state


@app.get("/", response_class=HTMLResponse)
def public_home(request: Request) -> str:
    articles = agent.storage.list_blog_posts(kind="article", limit=3)
    projects = agent.storage.list_blog_posts(kind="project", limit=3)
    wiki_notes = agent.storage.list_blog_posts(kind="wiki", limit=3)
    body = f"""
      <section class="panel">
        <h2>AI, разработка и вайбкодинг без лишнего шума</h2>
        <p>Здесь я публикую статьи про ИИ, разработку, автоматизацию и личные проекты. Часть проектов можно попробовать прямо на сайте в режиме ограниченного демо-доступа.</p>
        <div class="actions">
          <a class="btn primary" href="/projects">Попробовать проекты</a>
          <a class="btn" href="/blog">Читать статьи</a>
          <a class="btn" href="/wiki">Открыть Wiki</a>
          <a class="btn" href="https://t.me/AI_naMillion" target="_blank" rel="noopener">Telegram-канал</a>
        </div>
      </section>
      <section class="panel">
        <h2>Проекты</h2>
        <div class="blog-grid">{render_blog_cards(projects) or "<p>Проекты скоро появятся.</p>"}</div>
      </section>
      <section class="panel">
        <h2>Последние статьи</h2>
        <div class="blog-grid">{render_blog_cards(articles) or "<p>Статьи скоро появятся.</p>"}</div>
      </section>
      <section class="panel">
        <h2>Wiki-заметки</h2>
        <div class="blog-grid">{render_blog_cards(wiki_notes) or "<p>Wiki скоро наполнится.</p>"}</div>
      </section>
    """
    return public_shell(
        "AI на миллион",
        body,
        "Блог-лаборатория: статьи, AI-инструменты и проекты, которые можно попробовать.",
        request=request,
        path="/",
        description=seo_default_description(),
        schema=organization_schema(request),
    )


@app.get("/admin/topics", response_class=HTMLResponse)
def index(q: str = "") -> str:
    items = agent.storage.list_items(limit=50, query=q.strip() or None)
    viral_ideas_html = render_viral_cards(viral_state.get("ideas", []))
    rows = "\n".join(
        f"""
        <tr>
          <td><span class="pill">{item['score']:.2f}</span></td>
          <td>
            <strong>{escape(item['title'])}</strong>
            {"<span class=\"pill\">Apify</span>" if str(item.get("source") or "").startswith("Apify:") else ""}
            <br><small>{escape(item['source'])} · {escape(item['published_at'] or '')}</small>
            <br><small>{escape(item.get('summary') or '')[:220]}</small>
          </td>
          <td><a href="{escape(item['url'])}" target="_blank">источник</a></td>
          <td>
            <form method="post" action="/items/{item['id']}/draft">
              <select name="platform">
                <option value="telegram">Telegram</option>
                <option value="vk">ВКонтакте</option>
                <option value="vc">VC</option>
                <option value="dzen">Дзен</option>
                <option value="blog">Наш блог</option>
                <option value="blog_project">Проект на сайт</option>
                <option value="wiki">Wiki-заметка</option>
              </select>
              <button type="submit">Черновик</button>
            </form>
          </td>
        </tr>
        """
        for item in items
    )
    body = f"""
        <section class="panel">
          <div class="search-widget">
            <div id="clockProgress" class="clock-progress" style="--progress: 0%; --deg: 0;">
              <span id="progressValue">0%</span>
            </div>
            <div class="progress-meta">
              <strong id="progressStage">Поиск не запущен</strong>
              <small id="progressDetails">Нажми «Запустить поиск», чтобы обновить темы.</small>
            </div>
          </div>
          <div class="actions">
            <form method="post" action="/admin/viral/start">
              <button type="submit">Найти вирусные темы</button>
            </form>
            <span id="viralStage" class="pill">Вирусный анализ не запущен</span>
          </div>
        </section>
        <section class="panel" id="viralIdeasPanel">
          <h2>Вирусные идеи</h2>
          <p><small id="viralDetails">Агент оценит темы по хайпу, вечнозелёности, боли аудитории и практической пользе.</small></p>
          <div class="blog-grid" id="viralIdeas">{viral_ideas_html or "<p>Нажми «Найти вирусные темы», чтобы получить топ идей.</p>"}</div>
        </section>
        <section class="panel">
          <form class="toolbar" method="get" action="/admin/topics">
            <input type="search" name="q" value="{escape(q)}" placeholder="Поиск по темам, источникам и описаниям">
            <button type="submit">Найти</button>
            <a class="btn" href="/admin/topics">Сбросить</a>
          </form>
        </section>
        <section class="panel">
        <table>
          <thead><tr><th>Оценка</th><th>Тема</th><th>Ссылка</th><th>Действие</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        </section>
        <script>
          const runButton = document.querySelector('form[action="/admin/run"] button');
          const clock = document.getElementById('clockProgress');
          const progressValue = document.getElementById('progressValue');
          const progressStage = document.getElementById('progressStage');
          const progressDetails = document.getElementById('progressDetails');
          const viralButton = document.querySelector('form[action="/admin/viral/start"] button');
          const viralStage = document.getElementById('viralStage');
          const viralDetails = document.getElementById('viralDetails');
          const viralIdeas = document.getElementById('viralIdeas');

          function renderProgress(state) {{
            const progress = Number(state.progress || 0);
            clock.style.setProperty('--progress', `${{progress}}%`);
            clock.style.setProperty('--deg', String(progress * 3.6));
            progressValue.textContent = `${{progress}}%`;
            progressStage.textContent = state.stage || 'ожидание';
            const details = state.error
              ? `Ошибка: ${{state.error}}`
              : `Найдено: ${{state.fetched || 0}} · новых: ${{state.inserted || 0}}`;
            progressDetails.textContent = details;
            if (runButton) {{
              runButton.disabled = Boolean(state.running);
              runButton.textContent = state.running ? 'Ищу...' : 'Запустить поиск';
            }}
          }}

          async function pollProgress() {{
            const response = await fetch('/admin/run/status');
            const state = await response.json();
            renderProgress(state);
            if (state.running) setTimeout(pollProgress, 900);
          }}

          function renderViralIdea(idea) {{
            const evidence = (idea.evidence || []).map((item) => `<li>${{escapeHtml(item)}}</li>`).join('');
            const platform = idea.platform || 'telegram';
            const platformLabel = {{
              telegram: 'Telegram',
              vk: 'ВКонтакте',
              vc: 'VC',
              dzen: 'Дзен',
              blog: 'Блог',
              blog_project: 'Проект',
              wiki: 'Wiki',
            }}[platform] || platform;
            return `
              <article class="blog-card">
                <p><span class="pill">вирусность ${{idea.viral_score}}/100</span> <span class="pill">хайп ${{idea.hype}}/10</span> <span class="pill">вечнозелёность ${{idea.evergreen}}/10</span></p>
                <h3>${{escapeHtml(idea.title || '')}}</h3>
                <p><strong>Заход:</strong> ${{escapeHtml(idea.angle || '')}}</p>
                <p><strong>Слабое место:</strong> ${{escapeHtml(idea.weakness || '')}}</p>
                <ul>${{evidence}}</ul>
                <div class="actions">
                  <a class="btn" href="${{escapeAttr(idea.url || '#')}}" target="_blank" rel="noopener">Источник</a>
                  <form method="post" action="/items/${{Number(idea.item_id)}}/draft">
                    <input type="hidden" name="platform" value="${{escapeAttr(platform)}}">
                    <button type="submit">Черновик: ${{escapeHtml(platformLabel)}}</button>
                  </form>
                  <form method="post" action="/items/${{Number(idea.item_id)}}/draft">
                    <input type="hidden" name="platform" value="wiki">
                    <button type="submit">В Wiki</button>
                  </form>
                </div>
              </article>
            `;
          }}

          function escapeHtml(value) {{
            return String(value).replace(/[&<>"']/g, (char) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
          }}

          function escapeAttr(value) {{
            return escapeHtml(value).replace(/`/g, '&#96;');
          }}

          function renderViral(state) {{
            viralStage.textContent = state.stage || 'ожидание';
            viralDetails.textContent = state.error
              ? `Ошибка: ${{state.error}}`
              : `Прогресс: ${{Number(state.progress || 0)}}% · идей: ${{(state.ideas || []).length}}`;
            if (viralButton) {{
              viralButton.disabled = Boolean(state.running);
              viralButton.textContent = state.running ? 'Анализирую...' : 'Найти вирусные темы';
            }}
            if (state.ideas && state.ideas.length) {{
              viralIdeas.innerHTML = state.ideas.map(renderViralIdea).join('');
            }}
          }}

          async function pollViral() {{
            const response = await fetch('/admin/viral/status');
            const state = await response.json();
            renderViral(state);
            if (state.running) setTimeout(pollViral, 900);
          }}

          if (runButton) {{
            runButton.closest('form').addEventListener('submit', async (event) => {{
              event.preventDefault();
              await fetch('/admin/run/start', {{ method: 'POST' }});
              pollProgress();
            }});
          }}
          if (viralButton) {{
            viralButton.closest('form').addEventListener('submit', async (event) => {{
              event.preventDefault();
              await fetch('/admin/viral/start', {{ method: 'POST' }});
              pollViral();
            }});
          }}
          pollProgress();
          pollViral();
        </script>
    """
    return page_shell("AI-редактор", body, "Ежедневный поиск, рерайт, картинки и публикации под твой стиль.")


@app.get("/admin/sources", response_class=HTMLResponse)
@app.get("/sources", response_class=HTMLResponse)
def sources_page() -> str:
    sources = load_sources(settings.sources_path)
    rows = "\n".join(
        f"""
        <tr>
          <td>{escape(source.get('name', ''))}</td>
          <td>{escape(source.get('type', ''))}</td>
          <td>{source_link(source)}</td>
          <td>{escape(str(source.get('weight', 1.0)))}</td>
        </tr>
        """
        for source in sources
    )
    body = f"""
        <section class="panel">
        <form class="grid-form" method="post" action="/admin/sources">
          <div>
            <label>Название</label>
            <input name="name" placeholder="Например: AI канал">
          </div>
          <div>
            <label>Тип</label>
            <select name="source_type">
              <option value="rss">RSS</option>
              <option value="website">Сайт / карта ссылок</option>
              <option value="telegram">Telegram</option>
              <option value="apify_actor">Apify Actor</option>
            </select>
          </div>
          <div>
            <label>URL / канал / actor_id</label>
            <input name="url" placeholder="RSS URL, @channel или actor_id вроде wheat_tourist/ai-hype-tracker">
          </div>
          <div>
            <label>Вес</label>
            <input name="weight" value="1.0">
          </div>
          <div>
            <label>Apify query</label>
            <input name="query" placeholder="ai agents, vibe coding, openai">
          </div>
          <div>
            <label>Apify max items</label>
            <input type="number" name="max_items" min="1" max="100" value="20">
          </div>
          <button type="submit">Добавить</button>
        </form>
        </section>
        <section class="panel">
          <h2>Apify-пресеты для вирусных тем</h2>
          <p><small>Перед запуском добавь Apify API token в настройках. Эти actors помогут находить спрос: AI-тренды, Google News и Hacker News.</small></p>
          <div class="actions">
            <form method="post" action="/admin/sources/preset/apify">
              <button name="preset" value="ai_hype" type="submit">AI Hype Tracker</button>
              <button name="preset" value="google_news" type="submit">Google News Scraper</button>
              <button name="preset" value="hacker_news" type="submit">Hacker News Intelligence</button>
            </form>
            <form method="post" action="/admin/apify/run">
              <button type="submit">Собрать Apify сейчас</button>
            </form>
            <a class="btn" href="/admin/apify/results">Выдача Apify</a>
          </div>
        </section>
        <section class="panel">
        <table>
          <thead><tr><th>Название</th><th>Тип</th><th>URL</th><th>Вес</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        </section>
    """
    return page_shell("Источники", body, "Добавляй RSS, сайты и публичные Telegram-каналы для ежедневного поиска.")


@app.get("/admin/apify/results", response_class=HTMLResponse)
def apify_results(q: str = "") -> str:
    items = agent.storage.list_apify_items(limit=120, query=q.strip() or None)
    rows = "\n".join(render_apify_item_row(item) for item in items)
    body = f"""
      <section class="panel">
        <div class="actions">
          <form method="post" action="/admin/apify/run">
            <button type="submit">Собрать Apify сейчас</button>
          </form>
          <a class="btn" href="/admin/sources">Настроить sources</a>
          <a class="btn" href="/admin/settings">Apify token</a>
        </div>
        <form class="toolbar" method="get" action="/admin/apify/results">
          <input type="search" name="q" value="{escape(q)}" placeholder="Поиск внутри Apify-выдачи">
          <button type="submit">Найти</button>
          <a class="btn" href="/admin/apify/results">Сбросить</a>
        </form>
      </section>
      <section class="panel">
        <h2>Выдача Apify</h2>
        <p><small>Здесь показаны темы, которые пришли через Apify actors и уже сохранены в нашей базе. Их можно сразу отправлять в черновик.</small></p>
        <table>
          <thead><tr><th>Оценка</th><th>Тема</th><th>Actor / источник</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Apify-тем пока нет. Нажми «Собрать Apify сейчас».</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Apify", body, "Сырые источники и темы из Apify без похода в dashboard.")


def render_apify_item_row(item: dict) -> str:
    summary = escape(item.get("summary") or "")[:280]
    item_id = item.get("item_id")
    draft_action = (
        f"""
          <form method="post" action="/items/{item_id}/draft">
            <select name="platform">
              <option value="telegram">Telegram</option>
              <option value="blog">Наш блог</option>
              <option value="wiki">Wiki</option>
              <option value="vk">ВКонтакте</option>
              <option value="vc">VC</option>
              <option value="dzen">Дзен</option>
            </select>
            <button type="submit">Черновик</button>
          </form>
        """
        if item_id
        else '<small>Сначала нажми «Собрать Apify сейчас», чтобы добавить тему в базу.</small>'
    )
    return f"""
      <tr>
        <td><span class="pill">{float(item.get('score') or 0):.2f}</span></td>
        <td>
          <strong>{escape(item.get('title') or '')}</strong>
          <br><small>{summary}</small>
          <br><a href="{escape(item.get('url') or '#')}" target="_blank" rel="noopener">открыть источник</a>
        </td>
        <td><span class="pill">Apify</span><br><small>{escape(item.get('source') or '')}</small><br><small>{escape(item.get('actor_id') or '')}</small><br><small>{escape(item.get('published_at') or '')}</small></td>
        <td>
          {draft_action}
        </td>
      </tr>
    """


@app.post("/admin/apify/run")
async def run_apify_sources_now() -> HTMLResponse:
    sources = [source for source in load_sources(settings.sources_path) if source.get("type") == "apify_actor"]
    config = apify_config(agent.storage.get_settings_map(APIFY_SETTING_KEYS))
    fetched = 0
    inserted = 0
    errors: list[str] = []
    source_results: list[str] = []
    for source in sources:
        try:
            items = await run_apify_source(source, config)
        except (ApifyError, httpx.HTTPError) as exc:
            errors.append(f"{escape(source.get('name', 'Apify'))}: {escape(str(exc))}")
            continue
        fetched += len(items)
        new_for_source = 0
        for item in items:
            item["score"] = score_item(item, settings.keywords)
            agent.storage.save_apify_item(item)
            if agent.storage.upsert_item(item):
                inserted += 1
                new_for_source += 1
        source_results.append(
            f"{escape(source.get('name', 'Apify'))}: получено {len(items)}, новых {new_for_source}"
        )
    body = f"""
      <section class="panel">
        <h2>Apify сбор завершён</h2>
        <p><span class="pill">получено: {fetched}</span> <span class="pill">новых: {inserted}</span> <span class="pill">actors: {len(sources)}</span></p>
        <h3>Источники</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in source_results) or '<li>Apify sources не настроены.</li>'}</ul>
        <h3>Ошибки</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in errors) or '<li>Ошибок нет.</li>'}</ul>
        <div class="actions">
          <a class="btn" href="/admin/apify/results">Открыть выдачу Apify</a>
          <a class="btn" href="/admin/topics">Все темы</a>
        </div>
      </section>
    """
    return HTMLResponse(page_shell("Apify сбор", body, "Ручной запуск Apify-источников."))


@app.get("/admin/osint", response_class=HTMLResponse)
def osint_page(q: str = "", category: str = "") -> str:
    tools = agent.storage.list_osint_tools(
        limit=160,
        query=q.strip() or None,
        category=category.strip() or None,
    )
    categories = agent.storage.list_osint_categories()
    category_options = ["<option value=\"\">Все категории</option>"]
    for item in categories:
        selected = " selected" if item["category"] == category else ""
        category_options.append(
            f"<option value=\"{escape(item['category'])}\"{selected}>{escape(item['category'])} ({item['count']})</option>"
        )
    rows = "\n".join(render_osint_tool_row(tool) for tool in tools)
    body = f"""
      <section class="panel">
        <div class="actions">
          <form method="post" action="/admin/osint/run">
            <button type="submit">Обновить OSINT</button>
          </form>
          <a class="btn" href="https://osint.juanmathewsrebellosantos.com/" target="_blank" rel="noopener">OSINT Brasil</a>
          <a class="btn" href="https://github.com/jivoi/awesome-osint" target="_blank" rel="noopener">awesome-osint</a>
        </div>
        <p><small>Используем только публичные источники для фактчекинга, проверки ссылок, defensive research и поиска идей для материалов. Не используем для доксинга, обхода приватности и несанкционированного доступа.</small></p>
        <form class="toolbar" method="get" action="/admin/osint">
          <input type="search" name="q" value="{escape(q)}" placeholder="Поиск: Telegram, GitHub, fact checking, images">
          <select name="category">{"".join(category_options)}</select>
          <button type="submit">Найти</button>
          <a class="btn" href="/admin/osint">Сбросить</a>
        </form>
      </section>
      <section class="panel">
        <h2>OSINT-инструменты</h2>
        <p><small>Каталог можно использовать как базу идей: выбрать инструмент, открыть источник или сделать черновик статьи про сценарий применения.</small></p>
        <table>
          <thead><tr><th>Оценка</th><th>Инструмент</th><th>Категория</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">OSINT-каталог пуст. Нажми «Обновить OSINT».</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("OSINT", body, "Публичные инструменты для фактчекинга, ресёрча и идей для статей.")


def render_osint_tool_row(tool: dict) -> str:
    description = escape(tool.get("description") or "")[:320]
    item_id = tool.get("item_id")
    draft_action = (
        f"""
          <form method="post" action="/items/{item_id}/draft">
            <select name="platform">
              <option value="blog">Наш блог</option>
              <option value="telegram">Telegram</option>
              <option value="wiki">Wiki</option>
              <option value="vk">ВКонтакте</option>
              <option value="vc">VC</option>
              <option value="dzen">Дзен</option>
            </select>
            <button type="submit">Черновик</button>
          </form>
        """
        if item_id
        else "<small>Сначала нажми «Обновить OSINT», чтобы связать инструмент с темами.</small>"
    )
    return f"""
      <tr>
        <td><span class="pill">{float(tool.get('score') or 0):.0f}</span></td>
        <td>
          <strong>{escape(tool.get('name') or '')}</strong>
          <br><small>{description}</small>
          <br><a href="{escape(tool.get('url') or '#')}" target="_blank" rel="noopener">открыть инструмент</a>
        </td>
        <td><span class="pill">OSINT</span><br><small>{escape(tool.get('category') or '')}</small><br><small>{escape(tool.get('source') or '')}</small></td>
        <td>{draft_action}</td>
      </tr>
    """


@app.post("/admin/osint/run")
async def run_osint_import_now() -> HTMLResponse:
    fetched = 0
    inserted = 0
    errors: list[str] = []
    try:
        tools = await fetch_osint_tools()
    except httpx.HTTPError as exc:
        tools = []
        errors.append(escape(str(exc)))
    fetched = len(tools)
    agent.storage.clear_osint_catalog()
    for tool in tools:
        agent.storage.save_osint_tool(tool)
        item = osint_tool_to_item(tool)
        item["score"] = score_item(item, settings.keywords) + float(tool.get("score") or 0) / 10
        if agent.storage.upsert_item(item):
            inserted += 1
    body = f"""
      <section class="panel">
        <h2>OSINT импорт завершён</h2>
        <p><span class="pill">получено: {fetched}</span> <span class="pill">новых тем: {inserted}</span></p>
        <h3>Ошибки</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in errors) or '<li>Ошибок нет.</li>'}</ul>
        <div class="actions">
          <a class="btn" href="/admin/osint">Открыть OSINT</a>
          <a class="btn" href="/admin/topics">Все темы</a>
        </div>
      </section>
    """
    return HTMLResponse(page_shell("OSINT импорт", body, "Обновление публичного каталога инструментов."))


@app.post("/admin/sources")
@app.post("/sources")
async def create_source(
    name: str | None = Form(None),
    source_type: str | None = Form(None),
    url: str | None = Form(None),
    query: str = Form(""),
    max_items: int = Form(20),
    weight: float = Form(1.0),
) -> RedirectResponse:
    if not name or not url:
        return RedirectResponse("/admin/sources", status_code=303)
    add_source(
        settings.sources_path,
        {
            "name": name,
            "type": source_type or "rss",
            "url": url,
            "query": query,
            "max_items": max(1, min(max_items, 100)),
            "weight": weight,
        },
    )
    return RedirectResponse("/admin/sources", status_code=303)


def source_link(source: dict) -> str:
    if source.get("type") == "apify_actor":
        actor_id = source.get("actor_id") or source.get("url") or ""
        actor_url = f"https://apify.com/{actor_id}" if "/" in actor_id else "https://apify.com/store"
        query = f"<br><small>query: {escape(source.get('query') or '—')}</small>" if source.get("query") else ""
        max_items = f"<br><small>max: {escape(str(source.get('max_items') or '—'))}</small>"
        return f'<a href="{escape(actor_url)}" target="_blank" rel="noopener">{escape(actor_id)}</a>{query}{max_items}'
    url = source.get("url", "")
    return f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(url)}</a>'


@app.post("/admin/sources/preset/apify")
async def add_apify_preset(preset: str = Form(...)) -> RedirectResponse:
    presets = {
        "ai_hype": {
            "name": "Apify AI Hype Tracker",
            "type": "apify_actor",
            "actor_id": "wheat_tourist/ai-hype-tracker",
            "url": "wheat_tourist/ai-hype-tracker",
            "query": "AI agents, LLM, vibe coding, OpenAI, Claude, developer tools",
            "max_items": 20,
            "weight": 1.5,
        },
        "google_news": {
            "name": "Apify Google News AI",
            "type": "apify_actor",
            "actor_id": "futurizerush/google-news-scraper",
            "url": "futurizerush/google-news-scraper",
            "query": "AI agents OR LLM OR vibe coding OR OpenAI OR Claude",
            "max_items": 20,
            "weight": 1.3,
        },
        "hacker_news": {
            "name": "Apify Hacker News AI",
            "type": "apify_actor",
            "actor_id": "benthepythondev/hacker-news-intelligence",
            "url": "benthepythondev/hacker-news-intelligence",
            "query": "AI agents LLM developer tools",
            "max_items": 20,
            "weight": 1.4,
        },
    }
    source = presets.get(preset)
    if source:
        add_source(settings.sources_path, source)
    return RedirectResponse("/admin/sources", status_code=303)


@app.get("/blog", response_class=HTMLResponse)
def blog_home(request: Request) -> str:
    posts = agent.storage.list_blog_posts(limit=50)
    articles = [post for post in posts if post["kind"] == "article"]
    article_cards = render_blog_cards(articles)
    body = f"""
      <section class="panel">
        <h2>Статьи</h2>
        <div class="blog-grid">{article_cards or "<p>Статьи пока не опубликованы.</p>"}</div>
      </section>
    """
    return public_shell(
        "Блог",
        body,
        "Статьи про ИИ, разработку, вайбкодинг и личные проекты.",
        request=request,
        path="/blog",
        description="Статьи AI на миллион про искусственный интеллект, разработку, автоматизацию, AI-агентов и вайбкодинг.",
        schema=[
            organization_schema(request),
            breadcrumb_schema([("Главная", "/"), ("Блог", "/blog")], request),
        ],
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_home(request: Request) -> str:
    posts = agent.storage.list_blog_posts(limit=50)
    projects = [post for post in posts if post["kind"] == "project"]
    project_cards = render_blog_cards(projects)
    body = f"""
      <section class="panel">
        <h2>Проекты, которые можно попробовать</h2>
        <p><small>Модель: 3-5 бесплатных запусков на проект для одного посетителя. После лимита человек видит предложение написать автору, запросить доступ или дождаться расширенной версии.</small></p>
        <div class="blog-grid">{project_cards or "<p>Проекты пока не опубликованы.</p>"}</div>
      </section>
    """
    return public_shell(
        "Проекты",
        body,
        "Живые AI-инструменты и эксперименты, которые можно открыть и протестировать.",
        request=request,
        path="/projects",
        description="AI-проекты и инструменты от AI на миллион: демо, эксперименты и практические сервисы для теста.",
        schema=[
            organization_schema(request),
            breadcrumb_schema([("Главная", "/"), ("Проекты", "/projects")], request),
        ],
    )


@app.get("/wiki", response_class=HTMLResponse)
def wiki_home(request: Request) -> str:
    notes = agent.storage.list_blog_posts(kind="wiki", limit=100)
    body = f"""
      <section class="panel">
        <h2>Wiki</h2>
        <p><small>Вечнозелёные заметки по AI, агентам, вайбкодингу, автоматизации и личной инженерной системе.</small></p>
        <div class="blog-grid">{render_blog_cards(notes) or "<p>Wiki-заметок пока нет.</p>"}</div>
      </section>
    """
    return public_shell(
        "Wiki",
        body,
        "База знаний по AI-проектам, инструментам и рабочим практикам.",
        request=request,
        path="/wiki",
        description="Wiki AI на миллион: база знаний по AI-инструментам, агентам, автоматизации и вайбкодингу.",
        schema=[
            organization_schema(request),
            breadcrumb_schema([("Главная", "/"), ("Wiki", "/wiki")], request),
        ],
    )


def render_blog_cards(posts: list[dict]) -> str:
    cards = []
    for post in posts:
        post_path = "/projects" if post["kind"] == "project" else "/wiki" if post["kind"] == "wiki" else "/blog"
        cover_src = media_url(post.get("cover_path"), settings)
        cover = (
            f'<img class="blog-cover" src="{escape(cover_src)}" alt="{escape(post["title"])}">'
            if cover_src
            else ""
        )
        trial = (
            f'<p><span class="pill">{post["trial_limit"]} проб</span></p>'
            if post["kind"] == "project"
            else ""
        )
        kind_label = "проект" if post["kind"] == "project" else "wiki" if post["kind"] == "wiki" else "статья"
        cards.append(
            f"""
            <article class="blog-card">
              {cover}
              <p><span class="pill">{kind_label}</span></p>
              <h3><a href="{post_path}/{escape(post['slug'])}">{escape(post['title'])}</a></h3>
              <p>{escape(post.get('excerpt') or '')}</p>
              {trial}
            </article>
            """
        )
    return "\n".join(cards)


def render_viral_cards(ideas: list[dict]) -> str:
    cards = []
    for idea in ideas:
        evidence = "".join(f"<li>{escape(item)}</li>" for item in idea.get("evidence", []))
        platform = idea.get("platform", "telegram")
        cards.append(
            f"""
            <article class="blog-card">
              <p><span class="pill">вирусность {idea['viral_score']}/100</span> <span class="pill">хайп {idea['hype']}/10</span> <span class="pill">вечнозелёность {idea['evergreen']}/10</span></p>
              <h3>{escape(idea['title'])}</h3>
              <p><strong>Заход:</strong> {escape(idea['angle'])}</p>
              <p><strong>Слабое место:</strong> {escape(idea['weakness'])}</p>
              <ul>{evidence}</ul>
              <div class="actions">
                <a class="btn" href="{escape(idea['url'])}" target="_blank" rel="noopener">Источник</a>
                <form method="post" action="/items/{idea['item_id']}/draft">
                  <input type="hidden" name="platform" value="{escape(platform)}">
                  <button type="submit">Черновик: {escape(platform_label(platform))}</button>
                </form>
                <form method="post" action="/items/{idea['item_id']}/draft">
                  <input type="hidden" name="platform" value="wiki">
                  <button type="submit">В Wiki</button>
                </form>
              </div>
            </article>
            """
        )
    return "\n".join(cards)


@app.get("/admin/blog", response_class=HTMLResponse)
@app.get("/blog/admin", response_class=HTMLResponse)
def blog_admin() -> str:
    posts = agent.storage.list_blog_posts_admin(limit=500)
    rows = "\n".join(render_blog_admin_row(post) for post in posts)
    body = f"""
      <section class="panel">
        <h2>Новая публикация</h2>
        <form class="settings-form" method="post" action="/admin/blog">
          <label>Тип</label>
          <select name="kind">
            <option value="article">Статья</option>
            <option value="project">Проект с демо</option>
            <option value="wiki">Wiki-заметка</option>
          </select>
          <label>Заголовок</label>
          <input name="title" required placeholder="Например: Локальный AI-хаб для вайбкодинга">
          <label>Короткое описание</label>
          <textarea name="excerpt" style="min-height: 110px;" placeholder="1-2 предложения для карточки"></textarea>
          <label>Текст</label>
          <textarea name="content" placeholder="Статья, описание проекта, инструкция, выводы..."></textarea>
          <label>Demo URL</label>
          <input name="demo_url" placeholder="https://... или локальный URL проекта">
          <label>Лимит проб</label>
          <input type="number" name="trial_limit" min="1" max="20" value="5">
          <div class="actions"><button type="submit">Опубликовать в блог</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Опубликованные материалы</h2>
        <p><small>Здесь можно убрать дубли из публичного блога, отредактировать текст, заменить обложку или скрыть материал без удаления.</small></p>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Материал</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Медиа</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>{rows or '<tr><td colspan="6">Материалов пока нет.</td></tr>'}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Модель доступа</h2>
        <p><span class="pill">3-5 проб</span> человек может попробовать проект без регистрации. Дальше можно давать кнопку на заявку, донат, Telegram или расширенный доступ.</p>
        <p><span class="pill">Статья -> проект</span> статья объясняет идею, проект даёт живой опыт. Это лучше конвертирует, чем просто пост со ссылкой.</p>
      </section>
    """
    return page_shell("Управление блогом", body, "Публикуй статьи и проекты с ограниченным демо-доступом.")


def render_blog_admin_row(post: dict) -> str:
    public_path = blog_path_for_kind(post["kind"], post["slug"])
    cover = "есть" if post.get("cover_path") else "нет"
    status_label = {"published": "опубликовано", "hidden": "скрыто", "archived": "архив"}.get(
        post.get("status"),
        post.get("status") or "скрыто",
    )
    next_status = "hidden" if post.get("status") == "published" else "published"
    next_label = "Скрыть" if post.get("status") == "published" else "Опубликовать"
    return f"""
      <tr>
        <td>#{post['id']}</td>
        <td>
          <strong>{escape(post['title'])}</strong><br>
          <small>{escape(post['slug'])}</small>
        </td>
        <td>{escape(post['kind'])}</td>
        <td><span class="pill">{status_label}</span></td>
        <td>{cover}</td>
        <td>
          <div class="actions">
            <a class="btn" href="/admin/blog/{post['id']}/edit">Редактировать</a>
            <a class="btn" href="{escape(public_path)}" target="_blank" rel="noopener">Открыть</a>
            <form method="post" action="/admin/blog/{post['id']}/status">
              <input type="hidden" name="status" value="{next_status}">
              <button type="submit">{next_label}</button>
            </form>
            <form method="post" action="/admin/blog/{post['id']}/delete" onsubmit="return confirm('Удалить материал без восстановления?');">
              <button type="submit">Удалить</button>
            </form>
          </div>
        </td>
      </tr>
    """


@app.post("/admin/blog")
@app.post("/blog/admin")
async def create_blog_post(
    kind: str = Form("article"),
    title: str = Form(...),
    excerpt: str = Form(""),
    content: str = Form(...),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    clean_kind = kind if kind in {"article", "project", "wiki"} else "article"
    post_id = agent.storage.create_blog_post(
        title=title.strip(),
        slug=make_slug(title),
        kind=clean_kind,
        excerpt=excerpt.strip() or excerpt_from_content(content),
        content=clean_article_text(content),
        demo_url=demo_url.strip() or None,
        trial_limit=max(1, min(trial_limit, 20)),
    )
    slug = make_post_slug_by_id(post_id)
    if clean_kind == "wiki":
        export_wiki_markdown(title.strip(), slug, content)
    schedule_indexnow(blog_path_for_kind(clean_kind, slug))
    return RedirectResponse(blog_path_for_kind(clean_kind, slug), status_code=303)


@app.get("/admin/blog/{post_id}/edit", response_class=HTMLResponse)
def edit_blog_post(post_id: int) -> str:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    cover_src = media_url(post.get("cover_path"), settings)
    cover_block = (
        f"""
        <div class="panel" style="margin-top:10px;">
          <img src="{escape(cover_src or '')}" alt="Обложка" style="width:100%; max-width:520px; aspect-ratio:16/9; object-fit:cover; border-radius:8px; border:1px solid #e0d8ca;">
          <p><small>{escape(post.get('cover_path') or '')}</small></p>
          <form method="post" action="/admin/blog/{post_id}/cover/delete">
            <button type="submit">Удалить обложку</button>
          </form>
        </div>
        """
        if cover_src
        else "<p><small>Обложка не задана.</small></p>"
    )
    body = f"""
      <section class="panel">
        <div class="actions">
          <a class="btn" href="/admin/blog">Назад к блогу</a>
          <a class="btn" href="{escape(blog_path_for_kind(post['kind'], post['slug']))}" target="_blank" rel="noopener">Открыть публично</a>
        </div>
        <h2>Редактирование материала #{post_id}</h2>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/edit">
          <label>Тип</label>
          <select name="kind">
            {select_option('article', 'Статья', post['kind'])}
            {select_option('project', 'Проект с демо', post['kind'])}
            {select_option('wiki', 'Wiki-заметка', post['kind'])}
          </select>
          <label>Статус</label>
          <select name="status">
            {select_option('published', 'Опубликовано', post['status'])}
            {select_option('hidden', 'Скрыто', post['status'])}
            {select_option('archived', 'Архив', post['status'])}
          </select>
          <label>Заголовок</label>
          <input name="title" required value="{escape(post['title'])}">
          <label>Slug</label>
          <input name="slug" value="{escape(post['slug'])}">
          <label>Короткое описание</label>
          <textarea name="excerpt" style="min-height: 120px;">{escape(post.get('excerpt') or '')}</textarea>
          <label>Текст</label>
          <textarea name="content">{escape(post.get('content') or '')}</textarea>
          <label>Demo URL</label>
          <input name="demo_url" value="{escape(post.get('demo_url') or '')}">
          <label>Лимит проб</label>
          <input type="number" name="trial_limit" min="1" max="20" value="{int(post.get('trial_limit') or 5)}">
          <div class="actions">
            <button type="submit" class="primary">Сохранить</button>
          </div>
        </form>
      </section>
      <section class="panel">
        <h2>Медиа статьи</h2>
        {cover_block}
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/image/generate">
          <label>Сгенерировать новую обложку по теме</label>
          <input name="image_prompt" placeholder="Например: редакционный AI-агент анализирует новости, современный tech editorial, без текста и логотипов">
          <div class="actions"><button type="submit">Сгенерировать и поставить обложкой</button></div>
        </form>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/cover" enctype="multipart/form-data">
          <label>Загрузить новую обложку</label>
          <input type="file" name="cover_file" accept="image/*">
          <div class="actions"><button type="submit">Заменить обложку</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>AI-рерайт опубликованной статьи</h2>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/rewrite">
          <p><small>Берёт текущий текст статьи, активный стиль из <a href="/admin/styles">«Стили»</a> и перезаписывает опубликованный материал. Перед жёсткими правками можно сначала скрыть статью.</small></p>
          <label>Задача на рерайт</label>
          <textarea name="rewrite_instructions" style="min-height: 130px;" placeholder="Например: убери канцелярит, сделай живее, добавь авторский вывод, сохрани факты, без markdown и служебных блоков"></textarea>
          <div class="actions"><button type="submit">Переписать статью AI</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Опасная зона</h2>
        <form method="post" action="/admin/blog/{post_id}/delete" onsubmit="return confirm('Удалить материал, реакции, комментарии и статистику без восстановления?');">
          <button type="submit">Удалить материал полностью</button>
        </form>
      </section>
    """
    return page_shell(f"Редактирование: {post['title']}", body, "Правка опубликованной статьи, проекта или wiki-заметки.")


def select_option(value: str, label: str, current: str) -> str:
    selected = " selected" if value == current else ""
    return f'<option value="{escape(value)}"{selected}>{escape(label)}</option>'


@app.post("/admin/blog/{post_id}/edit")
async def update_blog_post(
    post_id: int,
    kind: str = Form("article"),
    status: str = Form("published"),
    title: str = Form(...),
    slug: str = Form(""),
    excerpt: str = Form(""),
    content: str = Form(...),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    clean_kind = kind if kind in {"article", "project", "wiki"} else "article"
    clean_status = status if status in {"published", "hidden", "archived"} else "published"
    clean_content = clean_article_text(content)
    clean_slug = re.sub(r"[^a-zA-Z0-9а-яА-Я]+", "-", (slug or title).lower()).strip("-")[:70]
    clean_slug = clean_slug or make_slug_for_post(title, post_id)
    if agent.storage.blog_slug_exists_for_other_post(clean_slug, post_id):
        clean_slug = make_slug_for_post(title, post_id)
    agent.storage.update_blog_post(
        post_id=post_id,
        title=title.strip(),
        slug=clean_slug,
        kind=clean_kind,
        excerpt=excerpt.strip() or excerpt_from_content(clean_content),
        content=clean_content,
        cover_path=post.get("cover_path"),
        demo_url=demo_url.strip() or None,
        trial_limit=max(1, min(trial_limit, 20)),
        status=clean_status,
    )
    if clean_kind == "wiki":
        export_wiki_markdown(title.strip(), clean_slug, clean_content, post.get("cover_path"), post.get("source_draft_id"))
    if clean_status == "published":
        schedule_indexnow(blog_path_for_kind(clean_kind, clean_slug))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@app.post("/admin/blog/{post_id}/status")
async def update_blog_post_status(post_id: int, status: str = Form("published")) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    clean_status = status if status in {"published", "hidden", "archived"} else "published"
    agent.storage.update_blog_post(
        post_id=post_id,
        title=post["title"],
        slug=post["slug"],
        kind=post["kind"],
        excerpt=post.get("excerpt") or "",
        content=post.get("content") or "",
        cover_path=post.get("cover_path"),
        demo_url=post.get("demo_url"),
        trial_limit=int(post.get("trial_limit") or 5),
        status=clean_status,
    )
    return RedirectResponse("/admin/blog", status_code=303)


@app.post("/admin/blog/{post_id}/cover")
async def update_blog_post_cover(post_id: int, cover_file: UploadFile | None = File(None)) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    if cover_file and cover_file.filename:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(cover_file.filename).suffix.lower() or ".png"
        filename = safe_filename(f"blog-{post_id}-{uuid4().hex}{suffix}")
        path = settings.media_dir / filename
        path.write_bytes(await cover_file.read())
        agent.storage.update_blog_post_cover(post_id, str(path))
        if post.get("status") == "published":
            schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@app.post("/admin/blog/{post_id}/cover/delete")
async def delete_blog_post_cover(post_id: int) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    agent.storage.update_blog_post_cover(post_id, None)
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@app.post("/admin/blog/{post_id}/image/generate")
async def generate_blog_post_cover(
    post_id: int,
    image_prompt: str = Form(""),
) -> Response:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    try:
        path, prompt, source = await generate_image_for_topic(
            post["title"],
            post.get("excerpt") or post.get("content") or "",
            image_prompt,
            settings,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Обложка не сгенерировалась",
                message=str(exc),
                detail="Проверь провайдера картинок в настройках или выбери fallback-режим.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.update_blog_post_cover(post_id, str(path))
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@app.post("/admin/blog/{post_id}/rewrite")
async def rewrite_blog_post(
    post_id: int,
    rewrite_instructions: str = Form(""),
) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    style_text = active_style_text()
    platform = "blog_project" if post["kind"] == "project" else "wiki" if post["kind"] == "wiki" else "blog"
    rewritten = await rewrite_draft(
        post.get("content") or "",
        platform,
        settings,
        style_text=style_text,
        rewrite_instructions=rewrite_instructions
        or "Сделай материал живее и читабельнее, сохрани факты, убери markdown, служебные слова и повторы.",
        ai_config=agent.ai_config(),
    )
    clean_content = clean_article_text(rewritten)
    agent.storage.update_blog_post(
        post_id=post_id,
        title=post["title"],
        slug=post["slug"],
        kind=post["kind"],
        excerpt=excerpt_from_content(clean_content),
        content=clean_content,
        cover_path=post.get("cover_path"),
        demo_url=post.get("demo_url"),
        trial_limit=int(post.get("trial_limit") or 5),
        status=post.get("status") or "published",
    )
    if post.get("kind") == "wiki":
        export_wiki_markdown(post["title"], post["slug"], clean_content, post.get("cover_path"), post.get("source_draft_id"))
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@app.post("/admin/blog/{post_id}/delete")
async def delete_blog_post(post_id: int) -> RedirectResponse:
    agent.storage.delete_blog_post(post_id)
    return RedirectResponse("/admin/blog", status_code=303)


def make_post_slug_by_id(post_id: int) -> str:
    post = agent.storage.get_blog_post(post_id)
    return post["slug"] if post else ""



@app.get("/blog/{slug}", response_class=HTMLResponse)
def view_blog_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/blog", "article", request)


@app.get("/projects/{slug}", response_class=HTMLResponse)
def view_project_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/projects", "project", request)


@app.get("/wiki/{slug}", response_class=HTMLResponse)
def view_wiki_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/wiki", "wiki", request)


def render_blog_post_response(
    slug: str, visitor_id: str | None, base_path: str, expected_kind: str, request: Request | None
) -> Response:
    post = agent.storage.get_blog_post_by_slug(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    if post["kind"] != expected_kind:
        return RedirectResponse(blog_path_for_kind(post["kind"], slug), status_code=303)
    visitor = visitor_id or uuid4().hex
    agent.storage.record_post_view(post["id"], visitor)
    cover_src = media_url(post.get("cover_path"), settings)
    cover = (
        f'<p><img class="blog-cover" src="{escape(cover_src)}" alt="{escape(post["title"])}"></p>'
        if cover_src
        else ""
    )
    project_action = ""
    if post["kind"] == "project":
        used = agent.storage.get_project_usage(post["id"], visitor)
        left = max(0, int(post["trial_limit"]) - used)
        project_action = f"""
          <section class="panel">
            <h2>Попробовать проект</h2>
            <p><span class="pill">Осталось запусков: {left} из {post['trial_limit']}</span></p>
            <form method="post" action="{base_path}/{escape(slug)}/try">
              <button class="primary" type="submit" {"disabled" if left <= 0 else ""}>Запустить демо</button>
            </form>
          </section>
        """
    body = f"""
      <section class="panel">
        {cover}
        <p><span class="pill">{'проект' if post['kind'] == 'project' else 'wiki' if post['kind'] == 'wiki' else 'статья'}</span></p>
        <div class="article-body">{render_article_html(post['content'])}</div>
      </section>
      {project_action}
      {render_engagement_block(post, visitor, base_path, request)}
    """
    canonical_path = blog_path_for_kind(post["kind"], post["slug"])
    cover_abs = absolute_site_url(cover_src, request) if cover_src else ""
    section_title = "Проекты" if post["kind"] == "project" else "Wiki" if post["kind"] == "wiki" else "Блог"
    section_path = "/projects" if post["kind"] == "project" else "/wiki" if post["kind"] == "wiki" else "/blog"
    response = HTMLResponse(
        public_shell(
            post["title"],
            body,
            post.get("excerpt") or "",
            request=request,
            path=canonical_path,
            description=post.get("excerpt") or post.get("content") or "",
            image_url=cover_abs,
            schema=[
                post_schema(post, request),
                breadcrumb_schema(
                    [("Главная", "/"), (section_title, section_path), (post["title"], canonical_path)],
                    request,
                ),
            ],
        )
    )
    if not visitor_id:
        response.set_cookie("visitor_id", visitor, max_age=60 * 60 * 24 * 365, httponly=True)
    return response


def render_engagement_block(
    post: dict, visitor: str, base_path: str, request: Request | None = None
) -> str:
    canonical_path = blog_path_for_kind(post["kind"], post["slug"])
    post_url = absolute_site_url(canonical_path, request)
    encoded_url = quote_plus(post_url)
    encoded_title = quote_plus(post["title"])
    reaction_stats = agent.storage.post_reaction_stats(post["id"], visitor)
    reaction_counts = reaction_stats["counts"]
    selected_reactions = reaction_stats["selected"]
    reactions = "\n".join(
        f"""
        <form method="post" action="{base_path}/{escape(post['slug'])}/react#engagement">
          <input type="hidden" name="reaction" value="{escape(key)}">
          <button type="submit" class="{'active' if key in selected_reactions else ''}">
            {escape(label)} · {reaction_counts.get(key, 0)}
          </button>
        </form>
        """
        for key, label in REACTIONS.items()
    )
    comments = agent.storage.list_post_comments(post["id"], limit=50)
    comment_cards = "\n".join(
        f"""
        <article class="comment-card">
          <strong>{escape(comment['author'] or 'Гость')}</strong>
          <p>{escape(comment['content'])}</p>
          <small>{escape(comment['created_at'])}</small>
        </article>
        """
        for comment in comments
    )
    views = agent.storage.post_view_stats(post["id"])
    share_text = f"{post['title']} {post_url}"
    escaped_share_text = escape(share_text)
    return f"""
      <section class="panel engagement" id="engagement">
        <div>
          <h2>Реакции и обсуждение</h2>
          <p>
            <span class="pill">{views['views']} просмотров</span>
            <span class="pill">{views['visitors']} читателей</span>
            <span class="pill">{len(comments)} комментариев</span>
          </p>
          <div class="reaction-grid">{reactions}</div>
          <form class="comment-form" method="post" action="{base_path}/{escape(post['slug'])}/comment#engagement">
            <label>Имя</label>
            <input name="author" maxlength="80" placeholder="Можно оставить пустым">
            <label>Комментарий</label>
            <textarea name="content" maxlength="2000" required placeholder="Что думаешь? Можно спорить, дополнять, предлагать тему."></textarea>
            <div class="actions"><button class="primary" type="submit">Оставить комментарий</button></div>
          </form>
          <div class="comment-list">{comment_cards or '<p><small>Комментариев пока нет. Можно быть первым.</small></p>'}</div>
        </div>
        <aside>
          <h2>Поделиться</h2>
          <div class="share-grid">
            <a class="btn" target="_blank" rel="noopener" href="https://t.me/share/url?url={encoded_url}&text={encoded_title}">Telegram</a>
            <a class="btn" target="_blank" rel="noopener" href="https://vk.com/share.php?url={encoded_url}&title={encoded_title}">VK</a>
            <a class="btn" target="_blank" rel="noopener" href="https://connect.ok.ru/offer?url={encoded_url}&title={encoded_title}">OK</a>
          </div>
          <input id="share-url" readonly value="{escape(post_url)}">
          <div class="actions">
            <button type="button" onclick="navigator.clipboard && navigator.clipboard.writeText(document.getElementById('share-url').value)">Скопировать ссылку</button>
          </div>
          <label>Текст для ручного шера</label>
          <textarea readonly style="min-height: 120px;">{escaped_share_text}</textarea>
        </aside>
      </section>
    """


async def react_to_post_response(
    slug: str, reaction: str, visitor_id: str | None, base_path: str, expected_kind: str
) -> Response:
    post = agent.storage.get_blog_post_by_slug(slug)
    if not post or post["kind"] != expected_kind:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    if reaction not in REACTIONS:
        raise HTTPException(status_code=400, detail="Неизвестная реакция")
    visitor = visitor_id or uuid4().hex
    agent.storage.toggle_post_reaction(post["id"], visitor, reaction)
    response = RedirectResponse(f"{base_path}/{escape(slug)}#engagement", status_code=303)
    if not visitor_id:
        response.set_cookie("visitor_id", visitor, max_age=60 * 60 * 24 * 365, httponly=True)
    return response


async def comment_post_response(
    slug: str,
    author: str,
    content: str,
    visitor_id: str | None,
    base_path: str,
    expected_kind: str,
) -> Response:
    post = agent.storage.get_blog_post_by_slug(slug)
    if not post or post["kind"] != expected_kind:
        raise HTTPException(status_code=404, detail="Публикация не найдена")
    clean_author = re.sub(r"\s+", " ", author).strip()[:80]
    clean_content = re.sub(r"\s+", " ", content).strip()[:2000]
    if not clean_content:
        raise HTTPException(status_code=400, detail="Комментарий пустой")
    visitor = visitor_id or uuid4().hex
    agent.storage.add_post_comment(post["id"], clean_author, clean_content)
    response = RedirectResponse(f"{base_path}/{escape(slug)}#engagement", status_code=303)
    if not visitor_id:
        response.set_cookie("visitor_id", visitor, max_age=60 * 60 * 24 * 365, httponly=True)
    return response


@app.post("/blog/{slug}/react")
async def react_blog_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/blog", "article")


@app.post("/projects/{slug}/react")
async def react_project_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/projects", "project")


@app.post("/wiki/{slug}/react")
async def react_wiki_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/wiki", "wiki")


@app.post("/blog/{slug}/comment")
async def comment_blog_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/blog", "article")


@app.post("/projects/{slug}/comment")
async def comment_project_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/projects", "project")


@app.post("/wiki/{slug}/comment")
async def comment_wiki_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/wiki", "wiki")


@app.post("/blog/{slug}/try")
async def try_blog_project(slug: str, visitor_id: str | None = Cookie(None)) -> Response:
    return await try_project_response(slug, visitor_id, "/blog")


@app.post("/projects/{slug}/try")
async def try_public_project(slug: str, visitor_id: str | None = Cookie(None)) -> Response:
    return await try_project_response(slug, visitor_id, "/projects")


async def try_project_response(slug: str, visitor_id: str | None, base_path: str) -> Response:
    post = agent.storage.get_blog_post_by_slug(slug)
    if not post or post["kind"] != "project":
        raise HTTPException(status_code=404, detail="Проект не найден")
    visitor = visitor_id or uuid4().hex
    used = agent.storage.get_project_usage(post["id"], visitor)
    if used >= int(post["trial_limit"]):
        body = f"""
          <section class="panel">
            <h2>Лимит проб закончился</h2>
            <p>Ты уже использовал {used} запусков проекта «{escape(post['title'])}». Дальше логика модели такая: заявка в Telegram, платный доступ или расширенная версия.</p>
            <div class="actions"><a class="btn" href="{base_path}/{escape(slug)}">Вернуться к проекту</a></div>
          </section>
        """
        response = HTMLResponse(public_shell("Лимит демо", body))
    else:
        new_count = agent.storage.record_project_use(post["id"], visitor)
        if post.get("demo_url"):
            response = RedirectResponse(post["demo_url"], status_code=303)
        else:
            body = f"""
              <section class="panel">
                <h2>Демо-запуск #{new_count}</h2>
                <p>Здесь будет встроенный запуск проекта. Пока demo URL не задан, поэтому агент только фиксирует использование.</p>
                <p><span class="pill">Осталось: {max(0, int(post['trial_limit']) - new_count)}</span></p>
                <div class="actions"><a class="btn" href="{base_path}/{escape(slug)}">Вернуться к проекту</a></div>
              </section>
            """
            response = HTMLResponse(public_shell("Демо проекта", body))
    if not visitor_id:
        response.set_cookie("visitor_id", visitor, max_age=60 * 60 * 24 * 365, httponly=True)
    return response


@app.get("/admin/settings", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    telegram_bot_token = saved_setting("telegram_bot_token", settings.telegram_bot_token or "")
    telegram_channel_ids = saved_setting("telegram_channel_ids", settings.telegram_channel_id or "")
    telegram_review_chat_id = saved_setting(
        "telegram_review_chat_id", settings.telegram_review_chat_id or ""
    )
    max_bot_token = saved_setting("max_bot_token")
    max_chat_ids = saved_setting("max_chat_ids")
    vk_access_token = saved_setting("vk_access_token", settings.vk_access_token or "")
    vk_owner_id = saved_setting("vk_owner_id", settings.vk_owner_id or "")
    ai_text_provider = saved_setting("ai_text_provider", "openrouter")
    ai_image_provider = saved_setting("ai_image_provider", "fallback")
    openrouter_saved_key = saved_setting("openrouter_api_key")
    openai_saved_key = saved_setting("openai_api_key")
    gemini_saved_key = saved_setting("gemini_api_key")
    openrouter_key_status = (
        "задан" if normalize_api_key(openrouter_saved_key) else "некорректный" if openrouter_saved_key else "не задан"
    )
    openai_key_status = (
        "задан"
        if normalize_api_key(openai_saved_key) or normalize_api_key(settings.openai_api_key)
        else "некорректный"
        if openai_saved_key or settings.openai_api_key
        else "не задан"
    )
    gemini_key_status = (
        "задан" if normalize_api_key(gemini_saved_key) else "некорректный" if gemini_saved_key else "не задан"
    )
    image_key_status = (
        "задан"
        if normalize_api_key(saved_setting("openai_image_api_key"))
        or normalize_api_key(settings.openai_api_key)
        else "не задан"
    )
    cloudflare_image_key_status = "задан" if saved_setting("cloudflare_image_api_key") else "не задан"
    muapi_key_status = "задан" if normalize_api_key(saved_setting("muapi_api_key")) else "не задан"
    apify_key_status = "задан" if saved_setting("apify_api_token") else "не задан"
    max_key_status = "задан" if max_bot_token else "не задан"
    apify_sources = [
        source for source in load_sources(settings.sources_path) if source.get("type") == "apify_actor"
    ]
    apify_options = "\n".join(
        f'<option value="{escape(source.get("name", ""))}">{escape(source.get("name", ""))} · {escape(source.get("actor_id") or source.get("url") or "")}</option>'
        for source in apify_sources
    )
    body = f"""
      <section class="panel">
        <h2>AI-операторы текста</h2>
        <form class="settings-form" method="post" action="/admin/settings/ai">
          <label>Провайдер статей и рерайта</label>
          <select name="ai_text_provider">
            <option value="openrouter" {"selected" if ai_text_provider == "openrouter" else ""}>OpenRouter</option>
            <option value="gemini" {"selected" if ai_text_provider == "gemini" else ""}>Google Gemini API</option>
            <option value="openai" {"selected" if ai_text_provider == "openai" else ""}>OpenAI</option>
            <option value="custom" {"selected" if ai_text_provider == "custom" else ""}>Custom OpenAI-compatible</option>
          </select>
          <label>OpenRouter API key</label>
          <input type="password" name="openrouter_api_key" placeholder="{'уже задано' if saved_setting('openrouter_api_key') else 'sk-or-...'}">
          <p class="hint">Статус OpenRouter API key: <strong>{openrouter_key_status}</strong>. После вставки ключа нажми именно «Сохранить AI-операторов».</p>
          <label>OpenRouter base URL</label>
          <input name="openrouter_base_url" value="{escape(saved_setting('openrouter_base_url', 'https://openrouter.ai/api/v1'))}">
          <label>OpenRouter model</label>
          <input name="openrouter_model" value="{escape(saved_setting('openrouter_model', 'openrouter/auto'))}" placeholder="openrouter/auto, anthropic/claude..., google/gemini...">
          <p class="hint">Рекомендация: оставь OpenRouter как основной оператор текста. Так можно менять модели без переписывания агента: авто-режим для ежедневных черновиков, сильную модель для VC-аналитики, быструю модель для Telegram.</p>

          <h2>Google Gemini API</h2>
          <label>Gemini API key</label>
          <input type="password" name="gemini_api_key" placeholder="{'уже задано' if saved_setting('gemini_api_key') else 'AIza...'}">
          <p class="hint">Статус Gemini API key: <strong>{gemini_key_status}</strong>. Используется нативный endpoint Google Generative Language API.</p>
          <label>Gemini base URL</label>
          <input name="gemini_base_url" value="{escape(saved_setting('gemini_base_url', 'https://generativelanguage.googleapis.com/v1beta'))}">
          <label>Gemini model</label>
          <input name="gemini_model" value="{escape(saved_setting('gemini_model', 'gemini-flash-latest'))}" placeholder="gemini-flash-latest, gemini-2.5-flash">

          <h2>Бесплатный резерв для текста</h2>
          <label>Fallback при лимите / ошибке основной модели</label>
          <select name="text_fallback_enabled">
            <option value="on" {"selected" if saved_setting('text_fallback_enabled', 'on') != "off" else ""}>Включён</option>
            <option value="off" {"selected" if saved_setting('text_fallback_enabled') == "off" else ""}>Выключен</option>
          </select>
          <label>OpenRouter free model</label>
          <input name="openrouter_free_model" value="{escape(saved_setting('openrouter_free_model', 'openrouter/free'))}" placeholder="openrouter/free или model-id:free">
          <p class="hint">Когда основной OpenRouter упирается в баланс или лимит, агент попробует бесплатный роутер. Качество и доступность могут плавать, зато это хороший аварийный режим.</p>
          <label>Hugging Face Router API key</label>
          <input type="password" name="huggingface_api_key" placeholder="{'уже задано' if saved_setting('huggingface_api_key') else 'hf_...'}">
          <label>Hugging Face base URL</label>
          <input name="huggingface_base_url" value="{escape(saved_setting('huggingface_base_url', 'https://router.huggingface.co/v1'))}">
          <label>Hugging Face model</label>
          <input name="huggingface_model" list="huggingface-model-presets" value="{escape(saved_setting('huggingface_model', 'deepseek-ai/DeepSeek-V3-0324:cheapest'))}" placeholder="deepseek-ai/DeepSeek-V3-0324:cheapest">
          <datalist id="huggingface-model-presets">
            <option value="deepseek-ai/DeepSeek-V3-0324:cheapest">DeepSeek V3 — хороший неризонинг для статей</option>
            <option value="openai/gpt-oss-120b:cheapest">GPT-OSS 120B — сильный open-weight, может рассуждать</option>
            <option value="Qwen/Qwen3-30B-A3B-Instruct-2507:cheapest">Qwen3 30B A3B — баланс цена/качество</option>
            <option value="Qwen/Qwen2.5-7B-Instruct:cheapest">Qwen2.5 7B — быстрый дешёвый резерв</option>
          </datalist>
          <p class="hint">Для рерайта без мусора сначала пробуем <code>deepseek-ai/DeepSeek-V3-0324:cheapest</code>. На Hugging Face нужны токен и право “Make calls to Inference Providers”.</p>

          <h2>OpenAI / совместимые модели текста</h2>
          <label>OpenAI API key</label>
          <input type="password" name="openai_api_key" placeholder="{'уже задано' if saved_setting('openai_api_key') or settings.openai_api_key else ''}">
          <p class="hint">Статус OpenAI API key: <strong>{openai_key_status}</strong>.</p>
          <label>OpenAI model</label>
          <input name="openai_model" value="{escape(saved_setting('openai_model', settings.openai_model))}">
          <label>Custom text base URL</label>
          <input name="custom_text_base_url" value="{escape(saved_setting('custom_text_base_url'))}" placeholder="http://localhost:11434/v1 или другой OpenAI-compatible URL">
          <label>Custom text API key</label>
          <input type="password" name="custom_text_api_key" placeholder="{'уже задано' if saved_setting('custom_text_api_key') else ''}">
          <label>Custom text model</label>
          <input name="custom_text_model" value="{escape(saved_setting('custom_text_model'))}">

          <h2>AI-оператор картинок</h2>
          <label>Провайдер картинок</label>
          <select name="ai_image_provider">
            <option value="fallback" {"selected" if ai_image_provider == "fallback" else ""}>Fallback-обложка без API</option>
            <option value="openrouter_images" {"selected" if ai_image_provider == "openrouter_images" else ""}>OpenRouter Images — качество</option>
            <option value="muapi_images" {"selected" if ai_image_provider == "muapi_images" else ""}>MuAPI Images / Video — 200+ моделей</option>
            <option value="cloudflare_worker_images" {"selected" if ai_image_provider == "cloudflare_worker_images" else ""}>Cloudflare Worker Images — бесплатно/быстро</option>
            <option value="openai_images" {"selected" if ai_image_provider == "openai_images" else ""}>OpenAI Images</option>
            <option value="custom_notes" {"selected" if ai_image_provider == "custom_notes" else ""}>Custom / внешний генератор</option>
          </select>
          <h2>MuAPI медиа</h2>
          <label>MuAPI API key</label>
          <input type="password" name="muapi_api_key" placeholder="{'уже задано' if saved_setting('muapi_api_key') else 'Sandbox или Production key'}">
          <p class="hint">Статус MuAPI key: <strong>{muapi_key_status}</strong>. Для реальной генерации нужна регистрация на MuAPI и ключ. Для тестов создай Sandbox key: он возвращает mock-результаты без списания кредитов.</p>
          <label>MuAPI base URL</label>
          <input name="muapi_base_url" value="{escape(saved_setting('muapi_base_url', 'https://api.muapi.ai'))}">
          <label>MuAPI image model endpoint</label>
          <select name="muapi_image_model">
            <option value="flux-dev-image" {"selected" if saved_setting('muapi_image_model', 'flux-dev-image') == "flux-dev-image" else ""}>Flux Dev — balanced</option>
            <option value="flux-schnell-image" {"selected" if saved_setting('muapi_image_model') == "flux-schnell-image" else ""}>Flux Schnell — fast/budget</option>
            <option value="seedream-v5-text-to-image" {"selected" if saved_setting('muapi_image_model') == "seedream-v5-text-to-image" else ""}>Seedream 5 — quality</option>
            <option value="nano-banana-text-to-image" {"selected" if saved_setting('muapi_image_model') == "nano-banana-text-to-image" else ""}>Nano Banana — Google image</option>
          </select>
          <label>MuAPI image aspect ratio</label>
          <select name="muapi_image_aspect_ratio">
            <option value="16:9" {"selected" if saved_setting('muapi_image_aspect_ratio', '16:9') == "16:9" else ""}>16:9 — обложка статьи</option>
            <option value="1:1" {"selected" if saved_setting('muapi_image_aspect_ratio') == "1:1" else ""}>1:1 — квадрат</option>
            <option value="4:5" {"selected" if saved_setting('muapi_image_aspect_ratio') == "4:5" else ""}>4:5 — соцсети</option>
            <option value="9:16" {"selected" if saved_setting('muapi_image_aspect_ratio') == "9:16" else ""}>9:16 — сторис/shorts</option>
          </select>
          <label>MuAPI image resolution</label>
          <input name="muapi_image_resolution" value="{escape(saved_setting('muapi_image_resolution', '1K'))}" placeholder="1K, 2K, 4K или значение модели">
          <label>MuAPI text-to-video endpoint</label>
          <input name="muapi_video_model" value="{escape(saved_setting('muapi_video_model', 'wan2.2-text-to-video'))}" placeholder="wan2.2-text-to-video, seedance-lite-t2v, kling-v3.0-standard-text-to-video">
          <label>MuAPI image-to-video endpoint</label>
          <input name="muapi_i2v_model" value="{escape(saved_setting('muapi_i2v_model', 'wan2.2-image-to-video'))}" placeholder="wan2.2-image-to-video, seedance-lite-i2v, kling-image-to-video">
          <label>MuAPI video aspect ratio</label>
          <select name="muapi_video_aspect_ratio">
            <option value="9:16" {"selected" if saved_setting('muapi_video_aspect_ratio', '9:16') == "9:16" else ""}>9:16 — Shorts/Reels</option>
            <option value="16:9" {"selected" if saved_setting('muapi_video_aspect_ratio') == "16:9" else ""}>16:9 — YouTube/обложка</option>
            <option value="1:1" {"selected" if saved_setting('muapi_video_aspect_ratio') == "1:1" else ""}>1:1 — квадрат</option>
          </select>
          <label>MuAPI video duration, sec</label>
          <input type="number" name="muapi_video_duration" min="3" max="15" value="{escape(saved_setting('muapi_video_duration', '5'))}">
          <p class="hint">MuAPI работает асинхронно: агент отправляет задачу, ждёт completion и сохраняет URL результата. Для видео-анонса лучше начинать с бюджетных Wan/Hailuo/Seedance Lite, а дорогие Veo/Kling Pro включать точечно.</p>
          <label>OpenRouter image model</label>
          <select name="openrouter_image_model">
            <option value="recraft/recraft-v3" {"selected" if saved_setting('openrouter_image_model', 'recraft/recraft-v3') == "recraft/recraft-v3" else ""}>Recraft V3 — редакционные обложки</option>
            <option value="black-forest-labs/flux.2-klein-4b" {"selected" if saved_setting('openrouter_image_model') == "black-forest-labs/flux.2-klein-4b" else ""}>FLUX.2 Klein 4B — дешевле</option>
            <option value="bytedance-seed/seedream-4.5" {"selected" if saved_setting('openrouter_image_model') == "bytedance-seed/seedream-4.5" else ""}>Seedream 4.5 — качество</option>
            <option value="google/gemini-2.5-flash-image" {"selected" if saved_setting('openrouter_image_model') == "google/gemini-2.5-flash-image" else ""}>Gemini / Nano Banana</option>
          </select>
          <label>OpenRouter aspect ratio</label>
          <select name="openrouter_image_aspect_ratio">
            <option value="16:9" {"selected" if saved_setting('openrouter_image_aspect_ratio', '16:9') == "16:9" else ""}>16:9 — обложка статьи</option>
            <option value="1:1" {"selected" if saved_setting('openrouter_image_aspect_ratio') == "1:1" else ""}>1:1 — квадрат</option>
            <option value="4:5" {"selected" if saved_setting('openrouter_image_aspect_ratio') == "4:5" else ""}>4:5 — соцсети</option>
            <option value="9:16" {"selected" if saved_setting('openrouter_image_aspect_ratio') == "9:16" else ""}>9:16 — сторис/вертикально</option>
          </select>
          <label>OpenRouter image size</label>
          <select name="openrouter_image_size">
            <option value="1K" {"selected" if saved_setting('openrouter_image_size', '1K') == "1K" else ""}>1K — оптимально</option>
            <option value="0.5K" {"selected" if saved_setting('openrouter_image_size') == "0.5K" else ""}>0.5K — дешевле, если модель поддерживает</option>
            <option value="2K" {"selected" if saved_setting('openrouter_image_size') == "2K" else ""}>2K — крупнее</option>
          </select>
          <p class="hint">Для красивых обложек выбирай OpenRouter Images + <code>recraft/recraft-v3</code> + 16:9 + 1K. Cloudflare Worker оставлен как бесплатный быстрый режим, но качество у него заметно слабее.</p>
          <label>Cloudflare Worker URL</label>
          <input name="cloudflare_image_worker_url" value="{escape(saved_setting('cloudflare_image_worker_url'))}" placeholder="https://free-image-generation-api.your-subdomain.workers.dev">
          <label>Cloudflare Worker API key</label>
          <input type="password" name="cloudflare_image_api_key" placeholder="{'уже задано' if saved_setting('cloudflare_image_api_key') else 'your-secret-api-key'}">
          <p class="hint">Статус Cloudflare Worker API key: <strong>{cloudflare_image_key_status}</strong>. Провайдер совместим с <code>saurav-z/free-image-generation-api</code>: POST JSON <code>{{"prompt": "..."}}</code>, ответ image/png.</p>
          <label>OpenAI Images API key</label>
          <input type="password" name="openai_image_api_key" placeholder="{'уже задано' if saved_setting('openai_image_api_key') or settings.openai_api_key else ''}">
          <p class="hint">Статус ключа картинок: <strong>{image_key_status}</strong>.</p>
          <label>OpenAI image model</label>
          <input name="openai_image_model" value="{escape(saved_setting('openai_image_model', 'gpt-image-1'))}">
          <label>Заметки по внешнему генератору картинок</label>
          <textarea name="custom_image_notes" style="min-height: 120px;" placeholder="Например: Replicate token, ComfyUI URL, Stability API, локальный workflow...">{escape(saved_setting('custom_image_notes'))}</textarea>
          <p class="hint">Для картинок лучше держать отдельного оператора: бесплатный fallback без API, дешёвые OpenRouter Images, свой Cloudflare Worker или будущий ComfyUI/Replicate workflow.</p>

          <div class="actions"><button type="submit">Сохранить AI-операторов</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Telegram</h2>
        <form class="settings-form" method="post" action="/admin/settings/publish">
          <label>Токен бота</label>
          <input type="password" name="telegram_bot_token" placeholder="{'уже задано' if telegram_bot_token else '123456:ABC...'}">
          <label>Канал(ы) для публикации</label>
          <textarea name="telegram_channel_ids" style="min-height: 120px;" placeholder="-100..., @channel или список через запятую">{escape(telegram_channel_ids)}</textarea>
          <label>Чат управления ботом</label>
          <input name="telegram_review_chat_id" value="{escape(telegram_review_chat_id)}" placeholder="ID личного чата для команд /run /topics /publish">

          <h2>MAX</h2>
          <label>MAX bot token</label>
          <input type="password" name="max_bot_token" placeholder="{'уже задано' if max_bot_token else 'токен из платформы MAX'}">
          <p class="hint">Статус MAX token: <strong>{max_key_status}</strong>. По официальной документации токен передаётся в заголовке <code>Authorization</code>, API-домен: <code>platform-api.max.ru</code>.</p>
          <label>MAX chat/channel ID</label>
          <textarea name="max_chat_ids" style="min-height: 90px;" placeholder="ID чата или канала, по одному на строку">{escape(max_chat_ids)}</textarea>
          <p class="hint">Для официальных каналов MAX нужна платформа партнёров: организация/ИП-резидент РФ, верификация, модерация бота и добавление бота в канал/чат. После этого сюда вставляем токен и ID.</p>
          <div class="actions"><button type="submit" formaction="/admin/settings/publish">Сохранить настройки</button></div>

          <h2>ВКонтакте</h2>
          <label>VK access token</label>
          <input type="password" name="vk_access_token" placeholder="{'уже задано' if vk_access_token else ''}">
          <label>VK owner_id</label>
          <input name="vk_owner_id" value="{escape(vk_owner_id)}" placeholder="-123 для группы или ID пользователя">

          <h2>VC</h2>
          <label>VC API token / cookie / session</label>
          <input type="password" name="vc_api_token" placeholder="{'уже задано' if saved_setting('vc_api_token') else ''}">
          <label>VC workspace / author id</label>
          <input name="vc_workspace_id" value="{escape(saved_setting('vc_workspace_id'))}">
          <p><small>Для VC сейчас используется подготовка черновика. Полный автопостинг лучше делать через отдельный Playwright-сценарий с твоей авторизованной сессией.</small></p>

          <h2>Дзен</h2>
          <label>Дзен API token / OAuth / RSS token</label>
          <input type="password" name="dzen_api_token" placeholder="{'уже задано' if saved_setting('dzen_api_token') else ''}">
          <label>Publisher / channel id</label>
          <input name="dzen_publisher_id" value="{escape(saved_setting('dzen_publisher_id'))}">
          <p class="hint">Для раздела «Свой сайт» в Дзене используй домен <code>https://agent.gazon59.ru/</code>, RSS-ленту <code>https://agent.gazon59.ru/rss.xml</code> и verification-файл <code>{ZEN_VERIFICATION_PATH}</code>.</p>

          <h2>Другие площадки</h2>
          <label>Заметки по API, токенам и правилам публикации</label>
          <textarea name="other_platforms" style="min-height: 160px;" placeholder="Например: Medium token, Substack workflow, личные правила публикации...">{escape(saved_setting('other_platforms'))}</textarea>

          <div class="actions">
            <button type="submit">Сохранить настройки</button>
          </div>
        </form>
        <form class="settings-form" method="post" action="/admin/max/test">
          <div class="actions"><button type="submit">Проверить MAX token</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Apify: внешние API-источники</h2>
        <form class="settings-form" method="post" action="/admin/settings/apify">
          <label>Apify API token</label>
          <input type="password" name="apify_api_token" placeholder="{'уже задано' if saved_setting('apify_api_token') else 'apify_api_...'}">
          <p class="hint">Статус Apify token: <strong>{apify_key_status}</strong>. Токен нужен для запуска actors из API Mega List: Google News, HN Intelligence, AI Hype Tracker, SEO keyword actors.</p>
          <label>Apify sources</label>
          <select name="apify_enabled">
            <option value="on" {"selected" if saved_setting('apify_enabled', 'on') != 'off' else ""}>Включены</option>
            <option value="off" {"selected" if saved_setting('apify_enabled') == 'off' else ""}>Выключены</option>
          </select>
          <label>Timeout, seconds</label>
          <input type="number" name="apify_timeout_seconds" min="20" max="300" value="{escape(saved_setting('apify_timeout_seconds', '90'))}">
          <label>Max items по умолчанию</label>
          <input type="number" name="apify_max_items" min="1" max="100" value="{escape(saved_setting('apify_max_items', '20'))}">
          <p class="hint">Рекомендация: держать 20 элементов на actor и запускать Apify только для “Найти вирусные темы” или ежедневного поиска. Некоторые actors платные, поэтому лимиты важны.</p>
          <div class="actions"><button type="submit">Сохранить Apify</button></div>
        </form>
        <form class="settings-form" method="post" action="/admin/apify/test">
          <label>Проверить Apify-источник</label>
          <select name="source_name">{apify_options or '<option value="">Сначала добавь Apify source</option>'}</select>
          <div class="actions"><button type="submit">Проверить actor</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Как это используется</h2>
        <p><span class="pill">Telegram</span> можно указать несколько каналов: каждый с новой строки или через запятую. При публикации пост уйдёт во все указанные каналы.</p>
        <p><span class="pill">VK</span> используется для прямой публикации через <code>wall.post</code>.</p>
        <p><span class="pill">VC / Дзен</span> пока хранят данные для будущего автопостинга и помогают держать всё в одном месте.</p>
      </section>
    """
    return page_shell("Настройки", body, "Площадки, каналы, токены API и параметры публикации.")


@app.get("/admin/seo", response_class=HTMLResponse)
def seo_page() -> str:
    values = agent.storage.get_settings_map(SEO_SETTING_KEYS)
    indexnow_key = values.get("indexnow_key") or secrets.token_hex(16)
    posts = agent.storage.list_blog_posts(limit=1000)
    public_count = len(public_url_entries())
    article_count = len([post for post in posts if post["kind"] == "article"])
    project_count = len([post for post in posts if post["kind"] == "project"])
    wiki_count = len([post for post in posts if post["kind"] == "wiki"])
    missing_cover = len([post for post in posts if not post.get("cover_path")])
    missing_excerpt = len([post for post in posts if not post.get("excerpt")])
    checks = [
        ("robots.txt", "/robots.txt", "готов"),
        ("sitemap.xml", "/sitemap.xml", f"{public_count} URL"),
        ("RSS для Дзена", "/rss.xml", "готов"),
        ("llms.txt", "/llms.txt", "готов"),
        ("llms-full.txt", "/llms-full.txt", "готов"),
        ("IndexNow key file", "/indexnow-key.txt", "готов" if values.get("indexnow_key") else "нужно сохранить ключ"),
    ]
    check_rows = "\n".join(
        f"""
        <tr>
          <td><strong>{escape(name)}</strong></td>
          <td><a href="{escape(path)}" target="_blank" rel="noopener">{escape(path)}</a></td>
          <td><span class="pill">{escape(status)}</span></td>
        </tr>
        """
        for name, path, status in checks
    )
    body = f"""
      <section class="panel">
        <h2>SEO-пульт</h2>
        <div class="stats">
          <div><strong>{public_count}</strong><span>публичных URL</span></div>
          <div><strong>{article_count}</strong><span>статей</span></div>
          <div><strong>{project_count}</strong><span>проектов</span></div>
          <div><strong>{wiki_count}</strong><span>wiki</span></div>
        </div>
        <p><span class="pill">обложек нет: {missing_cover}</span> <span class="pill">описаний нет: {missing_excerpt}</span></p>
        <table>
          <thead><tr><th>Файл</th><th>URL</th><th>Статус</th></tr></thead>
          <tbody>{check_rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Настройки продвижения</h2>
        <form class="settings-form" method="post" action="/admin/seo">
          <label>Базовый URL сайта</label>
          <input name="site_base_url" value="{escape(values.get('site_base_url') or 'https://agent.gazon59.ru')}">
          <label>Название сайта</label>
          <input name="seo_site_name" value="{escape(values.get('seo_site_name') or 'AI на миллион')}">
          <label>Описание по умолчанию</label>
          <textarea name="seo_default_description" style="min-height: 100px;">{escape(values.get('seo_default_description') or seo_default_description())}</textarea>

          <h2>Верификация поисковиков</h2>
          <label>Google site verification content</label>
          <input name="google_site_verification" value="{escape(values.get('google_site_verification') or '')}" placeholder="только content из meta-тега">
          <label>Yandex verification content</label>
          <input name="yandex_site_verification" value="{escape(values.get('yandex_site_verification') or '')}" placeholder="только content из meta-тега">
          <label>Bing msvalidate.01 content</label>
          <input name="bing_site_verification" value="{escape(values.get('bing_site_verification') or '')}" placeholder="только content из meta-тега">

          <h2>IndexNow</h2>
          <label>Автоотправка новых/обновлённых URL</label>
          <select name="indexnow_enabled">
            <option value="on" {"selected" if values.get('indexnow_enabled', 'on') != 'off' else ""}>Включена</option>
            <option value="off" {"selected" if values.get('indexnow_enabled') == 'off' else ""}>Выключена</option>
          </select>
          <label>IndexNow key</label>
          <input name="indexnow_key" value="{escape(indexnow_key)}">
          <p class="hint">После сохранения ключ будет доступен как <code>/indexnow-key.txt</code>. Агент сможет отправлять новые статьи в Bing/Яндекс через IndexNow.</p>

          <h2>Аналитика</h2>
          <label>Скрипт аналитики для публичных страниц</label>
          <textarea name="seo_analytics_script" style="min-height: 120px;" placeholder="<script defer src='...' data-website-id='...'></script>">{escape(values.get('seo_analytics_script') or '')}</textarea>
          <p class="hint">Сюда можно вставить Umami/Plausible/Яндекс Метрику. Скрипт добавляется только на публичные страницы сайта.</p>
          <div class="actions"><button type="submit">Сохранить SEO</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Активные действия</h2>
        <div class="actions">
          <form method="post" action="/admin/seo/indexnow">
            <button type="submit">Отправить все URL в IndexNow</button>
          </form>
          <a class="btn" href="https://search.google.com/search-console" target="_blank" rel="noopener">Google Search Console</a>
          <a class="btn" href="https://webmaster.yandex.ru/" target="_blank" rel="noopener">Яндекс Вебмастер</a>
          <a class="btn" href="https://www.bing.com/webmasters" target="_blank" rel="noopener">Bing Webmaster</a>
        </div>
      </section>
    """
    return page_shell("SEO", body, "Техническая индексация, AI-видимость, аналитика и быстрые сигналы поисковикам.")


@app.post("/admin/seo")
async def update_seo_settings(
    site_base_url: str = Form("https://agent.gazon59.ru"),
    seo_site_name: str = Form("AI на миллион"),
    seo_default_description_value: str = Form("", alias="seo_default_description"),
    google_site_verification: str = Form(""),
    yandex_site_verification: str = Form(""),
    bing_site_verification: str = Form(""),
    indexnow_enabled: str = Form("on"),
    indexnow_key: str = Form(""),
    seo_analytics_script: str = Form(""),
) -> RedirectResponse:
    values = {
        "site_base_url": site_base_url.strip().rstrip("/") or "https://agent.gazon59.ru",
        "seo_site_name": seo_site_name.strip() or "AI на миллион",
        "seo_default_description": seo_default_description_value.strip() or seo_default_description(),
        "google_site_verification": google_site_verification.strip(),
        "yandex_site_verification": yandex_site_verification.strip(),
        "bing_site_verification": bing_site_verification.strip(),
        "indexnow_enabled": "off" if indexnow_enabled == "off" else "on",
        "indexnow_key": re.sub(r"[^a-zA-Z0-9_-]", "", indexnow_key.strip()) or secrets.token_hex(16),
        "seo_analytics_script": seo_analytics_script.strip(),
    }
    for key, value in values.items():
        agent.storage.set_setting(key, value)
    return RedirectResponse("/admin/seo", status_code=303)


@app.post("/admin/seo/indexnow")
async def submit_all_indexnow(request: Request) -> HTMLResponse:
    urls = [entry["loc"] for entry in public_url_entries(request)]
    try:
        result = await submit_indexnow(urls, request)
    except httpx.HTTPError as exc:
        result = {"ok": False, "error": str(exc)}
    detail = escape(json.dumps(result, ensure_ascii=False, indent=2))
    body = f"""
      <section class="panel">
        <h2>{'Отправлено' if result.get('ok') else 'Ошибка отправки'}</h2>
        <p>URL: {len(urls)}</p>
        <pre>{detail}</pre>
        <div class="actions"><a class="btn" href="/admin/seo">Назад в SEO</a></div>
      </section>
    """
    return HTMLResponse(page_shell("IndexNow", body, "Ручная отправка URL в поисковые системы."))


@app.get("/admin/marketing", response_class=HTMLResponse)
def marketing_page() -> str:
    posts = agent.storage.list_blog_posts_admin(limit=500)
    articles = [post for post in posts if post.get("kind") == "article" and post.get("status") == "published"]
    projects = [post for post in posts if post.get("kind") == "project" and post.get("status") == "published"]
    wiki_notes = [post for post in posts if post.get("kind") == "wiki" and post.get("status") == "published"]
    missing_cover = [post for post in posts if post.get("status") == "published" and not post.get("cover_path")]
    missing_excerpt = [post for post in posts if post.get("status") == "published" and not post.get("excerpt")]
    duplicate_titles = duplicate_blog_titles(posts)
    context = read_product_marketing_context()
    context_block = (
        f'<pre style="white-space:pre-wrap; background:#fffaf2; border:1px solid #e0d8ca; border-radius:8px; padding:14px; max-height:420px; overflow:auto;">{escape(context)}</pre>'
        if context
        else "<p><small>Файл `.agents/product-marketing.md` пока не создан.</small></p>"
    )
    duplicate_rows = "\n".join(
        f"<li><strong>{escape(title)}</strong> - {count} публикации</li>"
        for title, count in duplicate_titles
    )
    launch_rows = "\n".join(render_project_launch_row(project) for project in projects[:8])
    body = f"""
      <section class="panel">
        <h2>Маркетинговый пульт</h2>
        <div class="stats">
          <div><strong>{len(articles)}</strong><span>статей</span></div>
          <div><strong>{len(projects)}</strong><span>проектов</span></div>
          <div><strong>{len(wiki_notes)}</strong><span>wiki</span></div>
          <div><strong>{len(missing_cover)}</strong><span>без обложки</span></div>
          <div><strong>{len(missing_excerpt)}</strong><span>без описания</span></div>
          <div><strong>{len(duplicate_titles)}</strong><span>дублей</span></div>
        </div>
        <div class="actions">
          <a class="btn" href="/admin/seo">SEO-пульт</a>
          <a class="btn" href="/admin/blog">Редактор блога</a>
          <a class="btn" href="/projects">Публичные проекты</a>
          <a class="btn" href="/rss.xml" target="_blank" rel="noopener">RSS</a>
        </div>
      </section>
      <section class="panel">
        <h2>Product Marketing Context</h2>
        <p><small>Основа для skills `product-marketing`, `content-strategy`, `ai-seo`, `cro`, `launch` и `directory-submissions`.</small></p>
        {context_block}
      </section>
      <section class="panel">
        <h2>Приоритетные действия</h2>
        <div class="blog-grid">
          {marketing_action_card("Очистить дубли", f"Групп дублей: {len(duplicate_titles)}", "/admin/blog")}
          {marketing_action_card("Довести карточки", f"Без обложки: {len(missing_cover)}, без описания: {len(missing_excerpt)}", "/admin/blog")}
          {marketing_action_card("AI SEO", "Добавить answer blocks, FAQ, schema и внутренние ссылки к сильным статьям.", "/admin/seo")}
          {marketing_action_card("Запуск проектов", "Подготовить demo, скриншоты, FAQ, short/long description.", "/projects")}
          {marketing_action_card("Каталоги", "Product Hunt, Futurepedia, TAAFT, Toolify, AlternativeTo, SaaSHub.", "https://www.skills.sh/topic/marketing")}
          {marketing_action_card("Аналитика", "Цели: переход в Telegram, запуск проекта, комментарий, публикация.", "/admin/seo")}
        </div>
      </section>
      <section class="panel">
        <h2>Дубли для проверки</h2>
        <ul>{duplicate_rows or "<li>Дублей по заголовкам не найдено.</li>"}</ul>
      </section>
      <section class="panel">
        <h2>Launch readiness проектов</h2>
        <table>
          <thead><tr><th>Проект</th><th>Demo</th><th>Обложка</th><th>Описание</th><th>Действие</th></tr></thead>
          <tbody>{launch_rows or '<tr><td colspan="5">Публичных проектов пока нет.</td></tr>'}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Чеклист следующего релиза</h2>
        <p><span class="pill">Product</span> выбрать 1 проект, который будем продвигать как флагман.</p>
        <p><span class="pill">SEO</span> сделать страницу проекта с одним H1, FAQ, SoftwareApplication schema и внутренними ссылками.</p>
        <p><span class="pill">AEO</span> добавить короткие ответы 40-60 слов на вопросы “что это”, “для кого”, “как попробовать”.</p>
        <p><span class="pill">Launch</span> подготовить 60-char, 150-word и 500-word описания, 5 скриншотов и короткое видео.</p>
        <p><span class="pill">Distribution</span> после готовности отправить в AI/SaaS/directories и сделать посты в Telegram/VK/VC/Дзен.</p>
      </section>
    """
    return page_shell("Маркетинг", body, "Позиционирование, SEO/AEO, запуск проектов, каталоги и рост аудитории.")


def duplicate_blog_titles(posts: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for post in posts:
        if post.get("status") != "published":
            continue
        title = re.sub(r"\s+", " ", (post.get("title") or "").strip().lower())
        if not title:
            continue
        counts[title] = counts.get(title, 0) + 1
    return [(title, count) for title, count in counts.items() if count > 1]


def marketing_action_card(title: str, text: str, href: str) -> str:
    target = ' target="_blank" rel="noopener"' if href.startswith("http") else ""
    return f"""
      <article class="blog-card">
        <h3>{escape(title)}</h3>
        <p>{escape(text)}</p>
        <a class="btn" href="{escape(href)}"{target}>Открыть</a>
      </article>
    """


def render_project_launch_row(project: dict) -> str:
    demo = "есть" if project.get("demo_url") else "нет"
    cover = "есть" if project.get("cover_path") else "нет"
    excerpt = "есть" if project.get("excerpt") else "нет"
    return f"""
      <tr>
        <td><strong>{escape(project['title'])}</strong><br><small>{escape(project['slug'])}</small></td>
        <td><span class="pill">{demo}</span></td>
        <td><span class="pill">{cover}</span></td>
        <td><span class="pill">{excerpt}</span></td>
        <td><a class="btn" href="/admin/blog/{project['id']}/edit">Редактировать</a></td>
      </tr>
    """


@app.get("/admin/growth", response_class=HTMLResponse)
def growth_page() -> str:
    links = agent.storage.list_growth_links(limit=60)
    tests = agent.storage.list_growth_tests(limit=80)
    drafts = agent.storage.list_drafts()[:10]
    links_rows = "\n".join(render_growth_link_row(link) for link in links)
    tests_rows = "\n".join(render_growth_test_row(test) for test in tests)
    draft_cards = "\n".join(render_growth_draft_card(draft) for draft in drafts)
    body = f"""
      <section class="panel">
        <h2>Лаборатория роста Telegram</h2>
        <p>Пульт для стратегии <strong>AI на миллион</strong>: рубрики, invite/UTM-ссылки, рекламные тесты и быстрые growth-brief для черновиков.</p>
        <div class="stats">
          <div><strong>{len(links)}</strong><span>ссылок входа</span></div>
          <div><strong>{len(tests)}</strong><span>тестов роста</span></div>
          <div><strong>{len(drafts)}</strong><span>черновиков</span></div>
        </div>
        <div class="actions">
          <a class="btn primary" href="https://t.me/AI_naMillion" target="_blank" rel="noopener">Открыть канал</a>
          <a class="btn" href="/docs/telegram_growth_strategy.md" target="_blank" rel="noopener">Стратегия</a>
          <a class="btn" href="/admin/marketing">Маркетинг</a>
          <a class="btn" href="/admin/publications">Журнал публикаций</a>
        </div>
      </section>
      <section class="panel">
        <h2>Рабочая модель</h2>
        <div class="blog-grid">
          {marketing_action_card("Личная лаборатория", "Показываем, что строим, что ломается и как применить AI без хайпа.", "/admin/editorial")}
          {marketing_action_card("Проекты с пробами", "3-5 запусков на сайте, затем CTA в Telegram за продолжением.", "/projects")}
          {marketing_action_card("Партнёрские тесты", "Каждый канал-донор получает отдельную invite-ссылку и запись в журнале.", "/admin/growth")}
          {marketing_action_card("MAX как зеркало", "Добавляем MAX в публикации и проверяем отдельную аудиторию.", "/admin/settings")}
        </div>
      </section>
      <section class="panel">
        <h2>Рубрики на неделю</h2>
        <table>
          <thead><tr><th>День</th><th>Утро</th><th>Вечер</th><th>CTA</th></tr></thead>
          <tbody>
            <tr><td>Пн</td><td>AI-находка дня</td><td>Вайбкодинг-дневник</td><td>Что тестировать дальше?</td></tr>
            <tr><td>Вт</td><td>Репозиторий дня</td><td>Разбор без хайпа</td><td>Хочешь схему?</td></tr>
            <tr><td>Ср</td><td>Промпт/чеклист</td><td>Проект можно попробовать</td><td>Попробовать демо</td></tr>
            <tr><td>Чт</td><td>AI для бизнеса</td><td>Ошибка/починка дня</td><td>Разобрать твою задачу?</td></tr>
            <tr><td>Пт</td><td>Инструмент недели</td><td>Публичный отчёт</td><td>Выбери следующий модуль</td></tr>
            <tr><td>Сб</td><td>Опрос</td><td>Лёгкий разбор</td><td>Голосование</td></tr>
            <tr><td>Вс</td><td>Дайджест</td><td>План недели</td><td>Поделиться постом</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Invite / UTM-ссылки</h2>
        <form class="settings-form" method="post" action="/admin/growth/links">
          <label>Название</label>
          <input name="name" placeholder="VC июнь, сайт, Дзен, партнёр @channel">
          <label>Ссылка</label>
          <input name="url" placeholder="https://t.me/+... или https://t.me/AI_naMillion?start=...">
          <label>Источник</label>
          <input name="source" placeholder="site, vc, dzen, vk, ads, mutual-pr">
          <label>Заметки</label>
          <textarea name="notes" style="min-height: 80px;" placeholder="Где используется, какой оффер, что проверить"></textarea>
          <div class="actions"><button type="submit">Добавить ссылку</button></div>
        </form>
        <table>
          <thead><tr><th>Название</th><th>Источник</th><th>Ссылка</th><th>Заметки</th><th></th></tr></thead>
          <tbody>{links_rows or '<tr><td colspan="5">Ссылок пока нет. Создай отдельные invite links для сайта, VC, Дзена, VK и каждого размещения.</td></tr>'}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Рекламные и партнёрские тесты</h2>
        <form class="settings-form" method="post" action="/admin/growth/tests">
          <label>Канал / партнёр</label>
          <input name="channel_name" placeholder="Название канала">
          <label>URL канала</label>
          <input name="channel_url" placeholder="https://t.me/...">
          <label>Сегмент аудитории</label>
          <input name="segment" placeholder="AI, no-code, бизнес, разработка, SMM">
          <label>Тип размещения</label>
          <select name="placement_type">
            <option value="direct">Прямое размещение</option>
            <option value="mutual-pr">Взаимопиар</option>
            <option value="telegram-ads">Telegram Ads</option>
            <option value="comment-seeding">Комментарии/посев</option>
          </select>
          <label>Стоимость, руб.</label>
          <input type="number" step="0.01" name="cost_rub" value="0">
          <label>Invite-ссылка</label>
          <input name="invite_url" placeholder="Отдельная ссылка под этот тест">
          <label>Статус</label>
          <select name="status">
            <option value="planned">Запланировано</option>
            <option value="running">В работе</option>
            <option value="done">Завершено</option>
            <option value="rejected">Не брать</option>
          </select>
          <label>Заметки и метрики</label>
          <textarea name="notes" style="min-height: 90px;" placeholder="Подписчики, просмотры, удержание, вывод"></textarea>
          <div class="actions"><button type="submit">Добавить тест</button></div>
        </form>
        <table>
          <thead><tr><th>Канал</th><th>Сегмент</th><th>Тип</th><th>Цена</th><th>Статус</th><th>Invite</th><th>Заметки</th><th></th></tr></thead>
          <tbody>{tests_rows or '<tr><td colspan="8">Тестов пока нет. Начни с 3-5 маленьких размещений.</td></tr>'}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Growth brief из черновика</h2>
        <p><small>Открывай черновик и нажимай «Telegram growth brief»: агент выдаст пост, CTA, варианты объявления до 160 символов и идеи интерактива.</small></p>
        <div class="control-list">{draft_cards or '<p>Черновиков пока нет.</p>'}</div>
      </section>
    """
    return page_shell("Рост Telegram", body, "Пульт подписок, рубрик, UTM, партнёров и MAX-зеркала.")


@app.post("/admin/growth/links")
async def add_growth_link(
    name: str = Form(""),
    url: str = Form(""),
    source: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    if name.strip() and url.strip():
        agent.storage.add_growth_link(name.strip(), url.strip(), source.strip(), notes.strip())
    return RedirectResponse("/admin/growth", status_code=303)


@app.post("/admin/growth/links/{link_id}/delete")
async def delete_growth_link(link_id: int) -> RedirectResponse:
    agent.storage.delete_growth_link(link_id)
    return RedirectResponse("/admin/growth", status_code=303)


@app.post("/admin/growth/tests")
async def add_growth_test(
    channel_name: str = Form(""),
    channel_url: str = Form(""),
    segment: str = Form(""),
    placement_type: str = Form("direct"),
    cost_rub: float = Form(0),
    invite_url: str = Form(""),
    status: str = Form("planned"),
    notes: str = Form(""),
) -> RedirectResponse:
    if channel_name.strip():
        agent.storage.add_growth_test(
            channel_name=channel_name.strip(),
            channel_url=channel_url.strip(),
            segment=segment.strip(),
            placement_type=placement_type.strip() or "direct",
            cost_rub=max(0, float(cost_rub or 0)),
            invite_url=invite_url.strip(),
            status=status.strip() or "planned",
            notes=notes.strip(),
        )
    return RedirectResponse("/admin/growth", status_code=303)


@app.post("/admin/growth/tests/{test_id}/delete")
async def delete_growth_test(test_id: int) -> RedirectResponse:
    agent.storage.delete_growth_test(test_id)
    return RedirectResponse("/admin/growth", status_code=303)


def render_growth_link_row(link: dict) -> str:
    return f"""
      <tr>
        <td><strong>{escape(link.get('name') or '')}</strong><br><small>{escape(link.get('created_at') or '')}</small></td>
        <td>{escape(link.get('source') or '')}</td>
        <td><a href="{escape(link.get('url') or '#')}" target="_blank" rel="noopener">открыть</a></td>
        <td>{escape(link.get('notes') or '')}</td>
        <td><form method="post" action="/admin/growth/links/{link['id']}/delete"><button type="submit">Удалить</button></form></td>
      </tr>
    """


def render_growth_test_row(test: dict) -> str:
    return f"""
      <tr>
        <td><strong>{escape(test.get('channel_name') or '')}</strong><br><small><a href="{escape(test.get('channel_url') or '#')}" target="_blank" rel="noopener">{escape(test.get('channel_url') or '')}</a></small></td>
        <td>{escape(test.get('segment') or '')}</td>
        <td>{escape(test.get('placement_type') or '')}</td>
        <td>{float(test.get('cost_rub') or 0):.0f} ₽</td>
        <td><span class="pill">{escape(test.get('status') or '')}</span></td>
        <td>{f'<a href="{escape(test.get("invite_url") or "#")}" target="_blank" rel="noopener">invite</a>' if test.get("invite_url") else '—'}</td>
        <td>{escape(test.get('notes') or '')}</td>
        <td><form method="post" action="/admin/growth/tests/{test['id']}/delete"><button type="submit">Удалить</button></form></td>
      </tr>
    """


def render_growth_draft_card(draft: dict) -> str:
    item = agent.storage.get_item(draft["item_id"]) or {}
    return f"""
      <article>
        <h3>Черновик #{draft['id']}</h3>
        <p>{escape(item.get('title') or title_from_content(draft.get('content') or ''))}</p>
        <div class="actions">
          <a class="btn" href="/drafts/{draft['id']}">Открыть</a>
          <form method="post" action="/drafts/{draft['id']}/growth"><button type="submit">Telegram growth brief</button></form>
        </div>
      </article>
    """


@app.post("/drafts/{draft_id}/growth")
async def draft_growth_brief(draft_id: int) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"]) or {}
    brief = build_telegram_growth_brief(draft, item)
    agent.storage.upsert_draft_variant(draft_id, "telegram_growth_brief", brief)
    body = f"""
      <section class="panel">
        <h2>Telegram growth brief для черновика #{draft_id}</h2>
        <textarea style="min-height: 620px;">{escape(brief)}</textarea>
        <div class="actions">
          <a class="btn primary" href="/drafts/{draft_id}">Вернуться к черновику</a>
          <a class="btn" href="/admin/growth">Пульт роста</a>
        </div>
      </section>
    """
    return HTMLResponse(page_shell("Telegram growth brief", body, "Пост, CTA, офферы и интерактив под рост канала."))


def build_telegram_growth_brief(draft: dict, item: dict) -> str:
    content = clean_article_text(draft.get("content") or "")
    title = item.get("title") or title_from_content(content)
    source_url = item.get("url") or ""
    summary = excerpt_from_content(content, 360)
    short_title = title[:90].rstrip()
    post = clean_article_text(
        f"""
        {short_title}

        Нашёл тему, которую стоит проверить не как новость, а как практический сценарий для AI на миллион.

        Суть простая: {summary}

        Что я проверю:
        1. где здесь реальная польза для бизнеса или вайбкодинга;
        2. можно ли превратить это в маленький проект или автоматизацию;
        3. где подвох, ограничения и лишний хайп.

        Если хотите, разберу это глубже и покажу схему реализации.

        Источник: {source_url}
        """
    )
    ad_1 = fit_ad_text("Собираю AI-агентов и показываю честно: что работает, что ломается, сколько времени экономит. Канал AI на миллион.")
    ad_2 = fit_ad_text("Вайбкодинг, Codex, Claude, n8n и боты на практике. Без пресс-релизов: только кейсы, схемы и выводы.")
    ad_3 = fit_ad_text(f"{short_title}. Разбираю, как применить AI-новости в бизнесе и проектах. AI на миллион.")
    return f"""Пост для Telegram

{post}

CTA

1. Огонь - тестировать дальше.
2. Сохраню - нужна инструкция.
3. Спорно - разобрать ограничения.
4. Напишите задачу в комментарии, я выберу одну и соберу схему агента.

Офферы для рекламы до 160 символов

1. {ad_1}
2. {ad_2}
3. {ad_3}

Идея интерактива

Опрос: что сделать следующим?
- короткий разбор;
- полноценную статью;
- демо-проект на сайте;
- схему агента для подписчиков.

Куда вести

- Канал: https://t.me/AI_naMillion
- Статья/источник: {source_url}
- Публичный сайт: {saved_setting("site_base_url", "https://agent.gazon59.ru")}
"""


def fit_ad_text(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", clean_article_text(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" .,;:") + "…"


@app.get("/admin/server", response_class=HTMLResponse)
@app.get("/server", response_class=HTMLResponse)
def server_page() -> str:
    rows = "\n".join(
        f"""
        <tr>
          <td>{escape(row['node'])}</td>
          <td>{escape(row['role'])}</td>
          <td>{escape(row['address'])}</td>
          <td>{escape(row['service'])}</td>
          <td>{escape(row['status'])}</td>
          <td>{escape(row['notes'])}</td>
        </tr>
        """
        for row in server_inventory()
    )
    body = f"""
      <section class="panel">
        <h2>Карта сервера и сервисов</h2>
        <table>
          <thead><tr><th>Нода</th><th>Роль</th><th>Адрес</th><th>Сервис</th><th>Статус</th><th>Заметки</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Текущий запуск AI-редактора</h2>
        <p><strong>Рабочая папка:</strong> <code>{escape(str(Path.cwd()))}</code></p>
        <p><strong>Команда:</strong> <code>PYTHONPATH=src python -u -m uvicorn vibe_agent.api:app --host 127.0.0.1 --port 8088 --loop asyncio</code></p>
        <p><strong>Screen-сессия:</strong> <code>vibe-agent</code></p>
        <p><strong>Логи:</strong> <code>.server.log</code></p>
      </section>
    """
    return page_shell("Сервер", body, "Карта нод, адресов и сервисов для дальнейшей работы.")


def server_inventory() -> list[dict[str, str]]:
    return [
        {
            "node": "Mac / локальная рабочая среда",
            "role": "Разработка и текущий запуск агента",
            "address": "http://127.0.0.1:8088",
            "service": "AI-редактор FastAPI / uvicorn",
            "status": "запущен",
            "notes": "Слушает порт 8088; запускается в screen-сессии vibe-agent из этой папки. Текущий PID меняется при рестарте.",
        },
        {
            "node": "Proxmox host",
            "role": "Сервер виртуализации",
            "address": "https://192.168.1.69:8006",
            "service": "Proxmox VE API/Web UI",
            "status": "доступен по сети, вход не выполнялся",
            "notes": "Панель отвечает pve-api-daemon. Данные о VM/LXC пока не считаны без учётных данных.",
        },
        {
            "node": "Proxmox VM/LXC для AI-редактора",
            "role": "Целевое постоянное размещение",
            "address": "пока не назначен",
            "service": "Docker Compose: vibe-content-agent",
            "status": "запланировано",
            "notes": "Проект уже содержит Dockerfile и docker-compose.yml для переноса.",
        },
    ]


@app.post("/admin/settings/publish")
@app.post("/settings/publish")
async def update_publish_settings(
    telegram_bot_token: str = Form(""),
    telegram_channel_ids: str = Form(""),
    telegram_review_chat_id: str = Form(""),
    max_bot_token: str = Form(""),
    max_chat_ids: str = Form(""),
    vk_access_token: str = Form(""),
    vk_owner_id: str = Form(""),
    vc_api_token: str = Form(""),
    vc_workspace_id: str = Form(""),
    dzen_api_token: str = Form(""),
    dzen_publisher_id: str = Form(""),
    other_platforms: str = Form(""),
) -> RedirectResponse:
    values = {
        "telegram_channel_ids": telegram_channel_ids,
        "telegram_review_chat_id": telegram_review_chat_id,
        "max_chat_ids": max_chat_ids,
        "vk_owner_id": vk_owner_id,
        "vc_workspace_id": vc_workspace_id,
        "dzen_publisher_id": dzen_publisher_id,
        "other_platforms": other_platforms,
    }
    for key, value in values.items():
        agent.storage.set_setting(key, value.strip())
    secret_values = {
        "telegram_bot_token": telegram_bot_token,
        "max_bot_token": max_bot_token,
        "vk_access_token": vk_access_token,
        "vc_api_token": vc_api_token,
        "dzen_api_token": dzen_api_token,
    }
    for key, value in secret_values.items():
        if value.strip():
            agent.storage.set_setting(key, value.strip())
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/max/test")
async def test_max_token() -> HTMLResponse:
    token = saved_setting("max_bot_token")
    if not token:
        return HTMLResponse(
            page_shell(
                "MAX test",
                '<section class="panel"><h2>MAX token не задан</h2><p>Добавь токен в настройках площадок.</p><div class="actions"><a class="btn" href="/admin/settings">Назад</a></div></section>',
            ),
            status_code=400,
        )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://platform-api.max.ru/me",
                headers={"Authorization": token},
            )
            response.raise_for_status()
            result = response.json()
        detail = escape(json.dumps(result, ensure_ascii=False, indent=2))
        body = f"""
          <section class="panel">
            <h2>MAX token работает</h2>
            <pre>{detail}</pre>
            <div class="actions"><a class="btn" href="/admin/settings">Назад</a></div>
          </section>
        """
        return HTMLResponse(page_shell("MAX test", body, "Проверка /me через platform-api.max.ru."))
    except httpx.HTTPError as exc:
        body = f"""
          <section class="panel">
            <h2>MAX token не прошёл проверку</h2>
            <p>{escape(str(exc))}</p>
            <div class="actions"><a class="btn" href="/admin/settings">Назад</a></div>
          </section>
        """
        return HTMLResponse(page_shell("MAX test", body), status_code=400)


@app.post("/admin/settings/apify")
async def update_apify_settings(
    apify_api_token: str = Form(""),
    apify_enabled: str = Form("on"),
    apify_timeout_seconds: int = Form(90),
    apify_max_items: int = Form(20),
) -> RedirectResponse:
    values = {
        "apify_enabled": "off" if apify_enabled == "off" else "on",
        "apify_timeout_seconds": str(max(20, min(apify_timeout_seconds, 300))),
        "apify_max_items": str(max(1, min(apify_max_items, 100))),
    }
    for key, value in values.items():
        agent.storage.set_setting(key, value)
    if apify_api_token.strip():
        agent.storage.set_setting("apify_api_token", apify_api_token.strip())
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/apify/test")
async def test_apify_source(source_name: str = Form("")) -> HTMLResponse:
    sources = load_sources(settings.sources_path)
    source = next(
        (
            item
            for item in sources
            if item.get("type") == "apify_actor" and item.get("name") == source_name
        ),
        None,
    )
    if not source:
        return HTMLResponse(
            page_shell(
                "Apify test",
                '<section class="panel"><h2>Источник не найден</h2><div class="actions"><a class="btn" href="/admin/settings">Назад</a></div></section>',
            )
        )
    try:
        items = await run_apify_source(
            source,
            apify_config(agent.storage.get_settings_map(APIFY_SETTING_KEYS)),
        )
        rows = "\n".join(
            f"""
            <tr>
              <td>{escape(item['title'])}</td>
              <td>{escape(item['source'])}</td>
              <td><a href="{escape(item['url'])}" target="_blank" rel="noopener">Открыть</a></td>
            </tr>
            """
            for item in items[:10]
        )
        body = f"""
          <section class="panel">
            <h2>Apify actor работает</h2>
            <p><span class="pill">получено: {len(items)}</span> <span class="pill">{escape(source.get('actor_id') or source.get('url') or '')}</span></p>
            <table><thead><tr><th>Тема</th><th>Источник</th><th>URL</th></tr></thead><tbody>{rows or '<tr><td colspan="3">Actor не вернул элементы.</td></tr>'}</tbody></table>
            <div class="actions"><a class="btn" href="/admin/settings">Назад</a></div>
          </section>
        """
    except (ApifyError, httpx.HTTPError) as exc:
        body = f"""
          <section class="panel">
            <h2>Apify actor не отработал</h2>
            <p>{escape(str(exc))}</p>
            <div class="actions"><a class="btn" href="/admin/settings">Назад</a></div>
          </section>
        """
    return HTMLResponse(page_shell("Apify test", body, "Проверка внешнего источника перед включением в ежедневный поиск."))


@app.post("/admin/settings/ai")
@app.post("/settings/ai")
async def update_ai_settings(
    ai_text_provider: str = Form("openrouter"),
    openrouter_api_key: str = Form(""),
    openrouter_base_url: str = Form("https://openrouter.ai/api/v1"),
    openrouter_model: str = Form("openrouter/auto"),
    gemini_api_key: str = Form(""),
    gemini_base_url: str = Form("https://generativelanguage.googleapis.com/v1beta"),
    gemini_model: str = Form("gemini-flash-latest"),
    text_fallback_enabled: str = Form("on"),
    openrouter_free_model: str = Form("openrouter/free"),
    huggingface_api_key: str = Form(""),
    huggingface_base_url: str = Form("https://router.huggingface.co/v1"),
    huggingface_model: str = Form("deepseek-ai/DeepSeek-V3-0324:cheapest"),
    openai_api_key: str = Form(""),
    openai_model: str = Form(settings.openai_model),
    custom_text_api_key: str = Form(""),
    custom_text_base_url: str = Form(""),
    custom_text_model: str = Form(""),
    ai_image_provider: str = Form("fallback"),
    openai_image_api_key: str = Form(""),
    openai_image_model: str = Form("gpt-image-1"),
    openrouter_image_model: str = Form("black-forest-labs/flux.2-klein-4b"),
    openrouter_image_aspect_ratio: str = Form("16:9"),
    openrouter_image_size: str = Form("1K"),
    cloudflare_image_worker_url: str = Form(""),
    cloudflare_image_api_key: str = Form(""),
    muapi_api_key: str = Form(""),
    muapi_base_url: str = Form("https://api.muapi.ai"),
    muapi_image_model: str = Form("flux-dev-image"),
    muapi_image_aspect_ratio: str = Form("16:9"),
    muapi_image_resolution: str = Form("1K"),
    muapi_video_model: str = Form("wan2.2-text-to-video"),
    muapi_i2v_model: str = Form("wan2.2-image-to-video"),
    muapi_video_aspect_ratio: str = Form("9:16"),
    muapi_video_duration: str = Form("5"),
    custom_image_notes: str = Form(""),
) -> RedirectResponse:
    text_provider = ai_text_provider if ai_text_provider in {"openrouter", "gemini", "openai", "custom"} else "openrouter"
    image_provider = (
        ai_image_provider
        if ai_image_provider
        in {"fallback", "openrouter_images", "muapi_images", "cloudflare_worker_images", "openai_images", "custom_notes"}
        else "fallback"
    )
    values = {
        "ai_text_provider": text_provider,
        "openrouter_base_url": openrouter_base_url.strip() or "https://openrouter.ai/api/v1",
        "openrouter_model": openrouter_model.strip() or "openrouter/auto",
        "gemini_base_url": gemini_base_url.strip() or "https://generativelanguage.googleapis.com/v1beta",
        "gemini_model": gemini_model.strip() or "gemini-flash-latest",
        "text_fallback_enabled": "off" if text_fallback_enabled == "off" else "on",
        "openrouter_free_model": openrouter_free_model.strip() or "openrouter/free",
        "huggingface_base_url": huggingface_base_url.strip() or "https://router.huggingface.co/v1",
        "huggingface_model": huggingface_model.strip() or "deepseek-ai/DeepSeek-V3-0324:cheapest",
        "openai_model": openai_model.strip() or settings.openai_model,
        "custom_text_base_url": custom_text_base_url.strip(),
        "custom_text_model": custom_text_model.strip(),
        "ai_image_provider": image_provider,
        "openai_image_model": openai_image_model.strip() or "gpt-image-1",
        "openrouter_image_model": openrouter_image_model.strip() or "recraft/recraft-v3",
        "openrouter_image_aspect_ratio": openrouter_image_aspect_ratio.strip() or "16:9",
        "openrouter_image_size": openrouter_image_size.strip() or "1K",
        "cloudflare_image_worker_url": cloudflare_image_worker_url.strip(),
        "muapi_base_url": muapi_base_url.strip() or "https://api.muapi.ai",
        "muapi_image_model": muapi_image_model.strip() or "flux-dev-image",
        "muapi_image_aspect_ratio": muapi_image_aspect_ratio.strip() or "16:9",
        "muapi_image_resolution": muapi_image_resolution.strip() or "1K",
        "muapi_video_model": muapi_video_model.strip() or "wan2.2-text-to-video",
        "muapi_i2v_model": muapi_i2v_model.strip() or "wan2.2-image-to-video",
        "muapi_video_aspect_ratio": muapi_video_aspect_ratio.strip() or "9:16",
        "muapi_video_duration": muapi_video_duration.strip() or "5",
        "custom_image_notes": custom_image_notes.strip(),
    }
    for key, value in values.items():
        agent.storage.set_setting(key, value)

    secret_values = {
        "openrouter_api_key": openrouter_api_key,
        "gemini_api_key": gemini_api_key,
        "huggingface_api_key": huggingface_api_key,
        "openai_api_key": openai_api_key,
        "custom_text_api_key": custom_text_api_key,
        "openai_image_api_key": openai_image_api_key,
        "cloudflare_image_api_key": cloudflare_image_api_key,
        "muapi_api_key": muapi_api_key,
    }
    for key, value in secret_values.items():
        if value.strip():
            agent.storage.set_setting(key, value.strip())
    return RedirectResponse("/admin/settings", status_code=303)


@app.get("/admin/styles", response_class=HTMLResponse)
@app.get("/styles", response_class=HTMLResponse)
def styles_page() -> str:
    active_style = agent.storage.get_setting("active_style", "base") or "base"
    styles = list_styles(settings)
    rows = "\n".join(
        f"""
        <tr>
          <td><span class="pill">{'активный' if style['id'] == active_style else 'профиль'}</span></td>
          <td><strong>{escape(style['title'])}</strong><br><small>{escape(style['id'])}</small></td>
          <td>{escape(style['preview'])}</td>
          <td>
            <form method="post" action="/admin/styles/active">
              <input type="hidden" name="style_id" value="{escape(style['id'])}">
              <button type="submit">Сделать активным</button>
            </form>
          </td>
        </tr>
        """
        for style in styles
    )
    options = "\n".join(
        f'<option value="{escape(style["id"])}" {"selected" if style["id"] == active_style else ""}>{escape(style["title"])}</option>'
        for style in styles
    )
    recent_drafts = agent.storage.list_drafts()[:6]
    recent_draft_links = "\n".join(
        f'<a class="btn" href="/drafts/{draft["id"]}">Черновик #{draft["id"]} · {escape(platform_label(draft.get("platform") or ""))}</a>'
        for draft in recent_drafts
    )
    body = f"""
      <section class="panel">
        <h2>Быстро вернуться</h2>
        <div class="actions">{recent_draft_links or '<a class="btn" href="/admin/control">К центру управления</a>'}</div>
        <p><small>После смены стиля открытый черновик не теряется. Вернись к нему здесь и нажми рерайт заново.</small></p>
      </section>
      <section class="panel">
        <h2>Новый профиль стиля</h2>
        <form method="post" action="/admin/styles">
          <label>Название</label>
          <input name="title" placeholder="Например: Telegram личный">
          <label>Правила, примеры, запреты, любимые обороты</label>
          <textarea name="content" style="min-height: 220px;" placeholder="Вставь сюда правила рерайта, примеры своих постов, тональность, структуру, стоп-слова..."></textarea>
          <div class="actions"><button type="submit">Сохранить стиль</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Прикрепить файл к стилю</h2>
        <form method="post" action="/admin/styles/upload" enctype="multipart/form-data">
          <label>К какому стилю добавить</label>
          <select name="style_id">{options}</select>
          <label>Файл .txt или .md</label>
          <input type="file" name="style_file" accept=".txt,.md,text/plain,text/markdown">
          <div class="actions"><button type="submit">Добавить файл к стилю</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Библиотека стилей</h2>
        <table>
          <thead><tr><th>Статус</th><th>Стиль</th><th>Фрагмент</th><th>Действие</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Идеи профилей</h2>
        <p><span class="pill">Telegram</span> быстрый личный пост, больше эмоции и опыта.</p>
        <p><span class="pill">VC</span> аналитика, структура, польза, риски, выводы.</p>
        <p><span class="pill">Дзен</span> шире и понятнее, меньше терминов, больше объяснений.</p>
      </section>
    """
    return page_shell("Стили", body, "Храни правила рерайта, примеры текстов и выбирай активный голос автора.")


@app.post("/admin/styles")
@app.post("/styles")
async def create_style(title: str = Form(...), content: str = Form(...)) -> RedirectResponse:
    style_id = save_style(settings, title, content)
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@app.post("/admin/styles/active")
@app.post("/styles/active")
async def set_active_style(style_id: str = Form(...)) -> RedirectResponse:
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@app.post("/admin/styles/upload")
@app.post("/styles/upload")
async def upload_style_file(
    style_id: str = Form(...),
    style_file: UploadFile | None = File(None),
) -> RedirectResponse:
    if not style_file or not style_file.filename:
        return RedirectResponse("/admin/styles", status_code=303)
    suffix = Path(style_file.filename).suffix.lower()
    if suffix not in {".txt", ".md"}:
        return RedirectResponse("/admin/styles", status_code=303)
    content = (await style_file.read()).decode("utf-8", errors="ignore")
    append_to_style(settings, style_id, style_file.filename, content[:20000])
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@app.get("/admin/style-memory", response_class=HTMLResponse)
def style_memory_page() -> str:
    rows = "\n".join(render_style_memory_row(item) for item in agent.storage.list_style_memory())
    body = f"""
      <section class="panel">
        <h2>Память стиля</h2>
        <p><small>Короткие правила, запреты, удачные фразы и примеры. Агент автоматически добавляет их к активному стилю при рерайте, AI Compare и версиях под площадки.</small></p>
        <form class="settings-form" method="post" action="/admin/style-memory">
          <label>Тип</label>
          <select name="kind">
            <option value="rule">Правило</option>
            <option value="ban">Запрет</option>
            <option value="phrase">Удачная фраза</option>
            <option value="example">Пример</option>
          </select>
          <label>Текст</label>
          <textarea name="content" style="min-height: 120px;" placeholder="Например: не начинать с 'в мире ИИ снова...', писать без markdown, добавлять личный вывод"></textarea>
          <label>Вес</label>
          <input type="number" name="weight" min="1" max="10" value="5">
          <div class="actions"><button type="submit">Добавить</button><a class="btn" href="/admin/styles">Профили стиля</a></div>
        </form>
      </section>
      <section class="panel">
        <h2>Текущая память</h2>
        <table>
          <thead><tr><th>Вес</th><th>Тип</th><th>Правило</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Память стиля пока пустая.</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Память стиля", body, "Маленькие правила, которые делают рерайт устойчивее.")


def render_style_memory_row(item: dict) -> str:
    labels = {
        "rule": "Правило",
        "ban": "Запрет",
        "phrase": "Удачная фраза",
        "example": "Пример",
    }
    return f"""
      <tr>
        <td><span class="pill">{int(item.get('weight') or 0)}</span></td>
        <td>{escape(labels.get(item.get('kind'), 'Правило'))}</td>
        <td>{escape(item.get('content') or '')}</td>
        <td>
          <form method="post" action="/admin/style-memory/{item['id']}/delete">
            <button type="submit">Удалить</button>
          </form>
        </td>
      </tr>
    """


@app.post("/admin/style-memory")
async def add_style_memory(
    kind: str = Form("rule"),
    content: str = Form(""),
    weight: int = Form(5),
) -> RedirectResponse:
    agent.storage.add_style_memory(kind, content, weight)
    return RedirectResponse("/admin/style-memory", status_code=303)


@app.post("/admin/style-memory/{memory_id}/delete")
async def delete_style_memory(memory_id: int) -> RedirectResponse:
    agent.storage.delete_style_memory(memory_id)
    return RedirectResponse("/admin/style-memory", status_code=303)


@app.get("/admin/task-notes", response_class=HTMLResponse)
def task_notes_page(status: str = "") -> str:
    clean_status = status if status in {"open", "waiting", "done"} else None
    notes = agent.storage.list_task_notes(status=clean_status, limit=160)
    rows = "\n".join(render_task_note_row(note) for note in notes)
    body = f"""
      <section class="panel">
        <h2>Task Notes</h2>
        <p><small>Короткие follow-up задачи агента и редактора. Это не таск-трекер, а список маленьких действий, чтобы материалы не терялись.</small></p>
        <div class="actions">
          <a class="btn" href="/admin/task-notes">Все</a>
          <a class="btn" href="/admin/task-notes?status=open">Открытые</a>
          <a class="btn" href="/admin/task-notes?status=waiting">Ожидают</a>
          <a class="btn" href="/admin/task-notes?status=done">Готово</a>
        </div>
        <form class="toolbar" method="post" action="/admin/task-notes">
          <input name="note_title" placeholder="Что сделать">
          <input name="note_content" placeholder="Детали">
          <input type="datetime-local" name="due_at">
          <button type="submit">Добавить</button>
        </form>
      </section>
      <section class="panel">
        <table>
          <thead><tr><th>Статус</th><th>Задача</th><th>Срок</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Задач пока нет.</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Task Notes", body, "Короткие follow-up действия без тяжёлого таск-трекера.")


@app.post("/admin/task-notes")
async def add_global_task_note(
    note_title: str = Form(""),
    note_content: str = Form(""),
    due_at: str = Form(""),
) -> RedirectResponse:
    title = note_title.strip() or "Задача"
    agent.storage.add_task_note(title, note_content, due_at=due_at.strip() or None)
    return RedirectResponse("/admin/task-notes", status_code=303)


@app.post("/admin/task-notes/{note_id}/status")
async def update_task_note_status(
    note_id: int,
    status: str = Form("open"),
    return_to: str = Form("/admin/task-notes"),
) -> RedirectResponse:
    agent.storage.update_task_note_status(note_id, status)
    return RedirectResponse(safe_return_path(return_to, "/admin/task-notes"), status_code=303)


@app.post("/admin/task-notes/{note_id}/delete")
async def delete_task_note(
    note_id: int,
    return_to: str = Form("/admin/task-notes"),
) -> RedirectResponse:
    agent.storage.delete_task_note(note_id)
    return RedirectResponse(safe_return_path(return_to, "/admin/task-notes"), status_code=303)


@app.get("/admin/model-cookbook", response_class=HTMLResponse)
def model_cookbook_page() -> str:
    entries = agent.storage.list_model_cookbook_entries()
    rows = "\n".join(render_model_cookbook_row(entry) for entry in entries)
    body = f"""
      <section class="panel">
        <h2>Model Cookbook</h2>
        <p><small>Карта моделей по ролям. Это не запускатор всего подряд, а шпаргалка: какая модель для чего, где endpoint, на каком железе держать.</small></p>
        <div class="actions">
          <form method="post" action="/admin/model-cookbook/seed"><button type="submit">Добавить пресеты</button></form>
          <a class="btn" href="/admin/settings">AI-настройки</a>
          <a class="btn" href="/admin/server">Сервер</a>
        </div>
        <form class="settings-form" method="post" action="/admin/model-cookbook">
          <label>Название</label>
          <input name="name" placeholder="Qwen 2.5 7B local">
          <label>Провайдер</label>
          <input name="provider" placeholder="Ollama, OpenRouter, Hugging Face, Gemini">
          <label>Роль</label>
          <input name="role" placeholder="быстрый рерайт, аналитика, заголовки, локальный fallback">
          <label>Endpoint</label>
          <input name="endpoint" placeholder="http://localhost:11434/v1 или https://openrouter.ai/api/v1">
          <label>Model ID</label>
          <input name="model_id" placeholder="qwen2.5:7b-instruct, google/gemini-2.5-flash">
          <label>Железо</label>
          <input name="hardware" placeholder="MacBook, Proxmox node, VPS, API">
          <label>Заметки</label>
          <textarea name="notes" style="min-height: 100px;" placeholder="Когда использовать, ограничения, качество, стоимость"></textarea>
          <label>Статус</label>
          <select name="status">
            <option value="candidate">Кандидат</option>
            <option value="active">Активная</option>
            <option value="fallback">Резерв</option>
            <option value="paused">Пауза</option>
          </select>
          <div class="actions"><button type="submit">Добавить</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Модели по ролям</h2>
        <table>
          <thead><tr><th>Статус</th><th>Модель</th><th>Роль</th><th>Endpoint</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="5">Cookbook пуст. Нажми «Добавить пресеты».</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Model Cookbook", body, "Карта моделей без лишнего комбайна.")


def render_model_cookbook_row(entry: dict) -> str:
    return f"""
      <tr>
        <td><span class="pill">{escape(entry.get('status') or '')}</span></td>
        <td><strong>{escape(entry.get('name') or '')}</strong><br><small>{escape(entry.get('provider') or '')} · {escape(entry.get('model_id') or '')}</small><br><small>{escape(entry.get('hardware') or '')}</small></td>
        <td>{escape(entry.get('role') or '')}<br><small>{escape(entry.get('notes') or '')}</small></td>
        <td><small>{escape(entry.get('endpoint') or '')}</small></td>
        <td>
          <form method="post" action="/admin/model-cookbook/{entry['id']}/delete">
            <button type="submit">Удалить</button>
          </form>
        </td>
      </tr>
    """


@app.post("/admin/model-cookbook")
async def add_model_cookbook_entry(
    name: str = Form(""),
    provider: str = Form(""),
    role: str = Form(""),
    endpoint: str = Form(""),
    model_id: str = Form(""),
    hardware: str = Form(""),
    notes: str = Form(""),
    status: str = Form("candidate"),
) -> RedirectResponse:
    if name.strip():
        agent.storage.add_model_cookbook_entry(
            name,
            provider=provider,
            role=role,
            endpoint=endpoint,
            model_id=model_id,
            hardware=hardware,
            notes=notes,
            status=status,
        )
    return RedirectResponse("/admin/model-cookbook", status_code=303)


@app.post("/admin/model-cookbook/seed")
async def seed_model_cookbook() -> RedirectResponse:
    existing = {
        (entry["provider"], entry["model_id"], entry["role"])
        for entry in agent.storage.list_model_cookbook_entries(limit=500)
    }
    presets = [
        {
            "name": "OpenRouter Auto",
            "provider": "OpenRouter",
            "role": "ежедневные черновики и обычный рерайт",
            "endpoint": "https://openrouter.ai/api/v1",
            "model_id": "openrouter/auto",
            "hardware": "API",
            "notes": "Главный универсальный режим, когда нужен баланс качества и скорости.",
            "status": "active",
        },
        {
            "name": "Gemini Flash",
            "provider": "Gemini",
            "role": "быстрый рерайт, короткие посты, идеи",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
            "model_id": "gemini-flash-latest",
            "hardware": "API",
            "notes": "Быстро и дёшево, полезно для Telegram и черновых вариантов.",
            "status": "candidate",
        },
        {
            "name": "DeepSeek V3 HF",
            "provider": "Hugging Face Router",
            "role": "бюджетный резерв для рерайта",
            "endpoint": "https://router.huggingface.co/v1",
            "model_id": "deepseek-ai/DeepSeek-V3-0324:cheapest",
            "hardware": "API",
            "notes": "Использовать как fallback, качество проверять через AI Compare.",
            "status": "fallback",
        },
        {
            "name": "Qwen 2.5 7B local",
            "provider": "Ollama",
            "role": "локальный быстрый резерв без внешних токенов",
            "endpoint": "http://localhost:11434/v1",
            "model_id": "qwen2.5:7b-instruct",
            "hardware": "MacBook / Proxmox",
            "notes": "Подходит для простых задач, заголовков, классификации и черновых подсказок.",
            "status": "candidate",
        },
        {
            "name": "Qwen 2.5 14B local",
            "provider": "Ollama",
            "role": "локальная аналитика, если хватает RAM/VRAM",
            "endpoint": "http://localhost:11434/v1",
            "model_id": "qwen2.5:14b-instruct",
            "hardware": "Proxmox node / мощный Mac",
            "notes": "Пробовать для Research Report и структурирования, если скорость приемлемая.",
            "status": "candidate",
        },
    ]
    for preset in presets:
        key = (preset["provider"], preset["model_id"], preset["role"])
        if key not in existing:
            agent.storage.add_model_cookbook_entry(**preset)
    return RedirectResponse("/admin/model-cookbook", status_code=303)


@app.post("/admin/model-cookbook/{entry_id}/delete")
async def delete_model_cookbook_entry(entry_id: int) -> RedirectResponse:
    agent.storage.delete_model_cookbook_entry(entry_id)
    return RedirectResponse("/admin/model-cookbook", status_code=303)


@app.get("/admin/schedule", response_class=HTMLResponse)
@app.get("/schedule", response_class=HTMLResponse)
def schedule_page() -> str:
    hour, minute = get_search_schedule()
    queued = agent.storage.list_publication_queue(limit=200)
    rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item['scheduled_at'])}</td>
          <td>{escape(platform_label(item['platform']))}</td>
          <td><span class="pill">{escape(status_label(item['status']))}</span></td>
          <td><strong>{escape(item['item_title'])}</strong><br><small>draft #{item['draft_id']}</small></td>
          <td>{escape(item.get('error') or '')}</td>
        </tr>
        """
        for item in queued
    )
    body = f"""
      <section class="panel">
        <h2>Расписание поиска</h2>
        <form class="toolbar" method="post" action="/admin/schedule/search">
          <label>
            Час
            <input type="number" name="hour" min="0" max="23" value="{hour}">
          </label>
          <label>
            Минута
            <input type="number" name="minute" min="0" max="59" value="{minute}">
          </label>
          <button type="submit">Сохранить расписание поиска</button>
        </form>
      </section>
      <section class="panel">
        <h2>Управление через Telegram</h2>
        <p>Напиши боту <strong>/start</strong> в личные сообщения, чтобы подключить этот чат к управлению агентом.</p>
        <table>
          <thead><tr><th>Команда</th><th>Что делает</th></tr></thead>
          <tbody>
            <tr><td><code>/status</code></td><td>показывает состояние агента</td></tr>
            <tr><td><code>/run</code></td><td>запускает поиск новостей и статей</td></tr>
            <tr><td><code>/topics</code></td><td>показывает топ тем с ID</td></tr>
            <tr><td><code>/draft 12 telegram</code></td><td>создаёт черновик по теме #12</td></tr>
            <tr><td><code>/publish 8</code></td><td>публикует черновик #8</td></tr>
            <tr><td><code>/queue</code></td><td>показывает отложенные публикации</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Подготовленные публикации</h2>
        <table>
          <thead><tr><th>Когда UTC</th><th>Площадка</th><th>Статус</th><th>Материал</th><th>Ошибка</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
    """
    return page_shell("Расписание", body, "Настрой ежедневный поиск и смотри очередь отложенных публикаций.")


@app.post("/admin/schedule/search")
@app.post("/schedule/search")
async def update_search_schedule(hour: int = Form(...), minute: int = Form(...)) -> RedirectResponse:
    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    agent.storage.set_setting("daily_run_hour", str(hour))
    agent.storage.set_setting("daily_run_minute", str(minute))
    if scheduler.running:
        install_search_job()
    return RedirectResponse("/admin/schedule", status_code=303)


@app.post("/items/{item_id}/draft", response_class=HTMLResponse)
async def create_draft(item_id: int, platform: str = Form(...)) -> str:
    try:
        draft = await agent.draft(item_id, platform)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft["draft_id"], platform, draft["content"])


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def view_draft(draft_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    return render_draft_page(draft_id, draft["platform"], draft["content"])


@app.post("/drafts/{draft_id}/rewrite", response_class=HTMLResponse)
async def rewrite_existing_draft(
    draft_id: int,
    content: str = Form(...),
    rewrite_instructions: str = Form(""),
) -> str:
    current_draft = agent.storage.get_draft(draft_id)
    if not current_draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_before_rewrite = clean_article_text(content)
    agent.storage.save_draft_revision(
        draft_id,
        clean_before_rewrite,
        "Перед рерайтом",
    )
    style_text = active_style_text()
    try:
        draft = await agent.rewrite(
            draft_id,
            clean_before_rewrite,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft["draft_id"], draft["platform"], draft["content"])


@app.post("/drafts/{draft_id}/history/{revision_id}/restore", response_class=HTMLResponse)
async def restore_draft_revision(draft_id: int, revision_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    revision = agent.storage.get_draft_revision(revision_id, draft_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    agent.storage.save_draft_revision(draft_id, draft["content"], "Перед откатом")
    restored_content = clean_article_text(revision["content"])
    agent.storage.update_draft_content(draft_id, restored_content)
    return render_draft_page(draft_id, draft["platform"], restored_content)


@app.post("/drafts/{draft_id}/variants", response_class=HTMLResponse)
async def generate_draft_variants(
    draft_id: int,
    content: str = Form(...),
    destinations: Annotated[list[str], Form()] = [],
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    selected = [item for item in destinations if item in DESTINATION_PLATFORMS]
    if not selected:
        selected = list(DESTINATION_PLATFORMS)
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content)
    style_text = active_style_text()
    for destination in selected:
        await agent.generate_variant(draft_id, destination, clean_content, style_text=style_text)
    return render_draft_page(draft_id, draft["platform"], clean_content)


@app.post("/drafts/{draft_id}/compare", response_class=HTMLResponse)
async def generate_draft_compare(
    draft_id: int,
    content: str = Form(...),
    rewrite_instructions: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_content = clean_article_text(content)
    agent.storage.save_draft_revision(draft_id, clean_content, "Перед AI Compare")
    style_text = active_style_text()
    try:
        await agent.compare_rewrites(
            draft_id,
            clean_content,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
            limit=3,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft_id, draft["platform"], clean_content)


@app.post("/drafts/{draft_id}/compare/{variant_id}/apply", response_class=HTMLResponse)
async def apply_draft_compare_variant(draft_id: int, variant_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    variant = agent.storage.get_draft_compare_variant(draft_id, variant_id)
    if not variant:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    agent.storage.save_draft_revision(draft_id, draft["content"], "Перед применением AI Compare")
    content = clean_article_text(variant["content"])
    agent.storage.update_draft_content(draft_id, content)
    agent.storage.mark_draft_compare_variant_selected(draft_id, variant_id)
    return render_draft_page(draft_id, draft["platform"], content)


@app.post("/drafts/{draft_id}/research", response_class=HTMLResponse)
async def generate_draft_research_report(
    draft_id: int,
    content: str = Form(""),
    research_question: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    clean_content = clean_article_text(content) or clean_article_text(draft["content"])
    agent.storage.update_draft_content(draft_id, clean_content)
    report = build_research_report(draft, item, clean_content, research_question)
    agent.storage.add_research_report(draft_id, draft["item_id"], f"Research: {item['title']}", report)
    agent.storage.add_task_note(
        "Проверить факты перед публикацией",
        "Открыть источник, сверить даты/цифры и добавить авторский вывод после Research Report.",
        draft_id=draft_id,
        item_id=draft["item_id"],
    )
    return render_draft_page(draft_id, draft["platform"], clean_content)


@app.post("/drafts/{draft_id}/notes", response_class=HTMLResponse)
async def add_draft_task_note(
    draft_id: int,
    note_title: str = Form(""),
    note_content: str = Form(""),
    due_at: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    title = note_title.strip() or "Задача по черновику"
    agent.storage.add_task_note(
        title,
        note_content,
        draft_id=draft_id,
        item_id=draft["item_id"],
        due_at=due_at.strip() or None,
    )
    return render_draft_page(draft_id, draft["platform"], draft["content"])


@app.post("/drafts/{draft_id}/image/generate", response_class=HTMLResponse)
async def generate_draft_image(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    try:
        path, prompt, source = await generate_image_for_topic(
            item["title"],
            item.get("summary", ""),
            image_prompt,
            settings,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Картинка не сгенерировалась",
                message=str(exc),
                detail="Проверь провайдера в настройках или временно выбери fallback-обложку без API.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=str(path),
        prompt=prompt,
        source=source,
    )
    return render_draft_page(draft_id, draft["platform"], content)


@app.post("/drafts/{draft_id}/image/generate-batch", response_class=HTMLResponse)
async def generate_draft_image_batch(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    created = 0
    errors: list[str] = []
    for index in range(4):
        try:
            path, prompt, source = await generate_image_for_topic(
                item["title"],
                item.get("summary", ""),
                f"{image_prompt}\nVariant {index + 1}: unique composition, same article idea.".strip(),
                settings,
                image_config=image_generation_config(),
            )
        except ImageGenerationError as exc:
            errors.append(str(exc))
            continue
        agent.storage.save_media_asset(
            draft_id=draft_id,
            item_id=draft["item_id"],
            path=str(path),
            prompt=prompt,
            source=f"{source}_batch",
            kind="image",
        )
        created += 1
    if created == 0:
        return HTMLResponse(
            publish_result_page(
                title="Варианты обложки не сгенерировались",
                message=errors[0] if errors else "Провайдер не вернул изображения.",
                detail="Проверь MuAPI/OpenRouter/Cloudflare настройки или верни fallback.",
                is_error=True,
            ),
            status_code=200,
        )
    return render_draft_page(draft_id, draft["platform"], content)


@app.post("/drafts/{draft_id}/video/generate", response_class=HTMLResponse)
async def generate_draft_video(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    try:
        video_url, prompt, source = await generate_video_for_topic(
            item["title"],
            item.get("summary", ""),
            image_prompt,
            image_url=None,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Видео-анонс не сгенерировался",
                message=str(exc),
                detail="Для видео нужен MuAPI key. Sandbox key подойдёт для проверки механики без списания кредитов.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=video_url,
        prompt=prompt,
        source=source,
        kind="video",
    )
    return render_draft_page(draft_id, draft["platform"], content)


@app.post("/drafts/{draft_id}/image/upload", response_class=HTMLResponse)
async def upload_draft_image(
    draft_id: int,
    content: str = Form(...),
    image_file: UploadFile | None = File(None),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    if not image_file or not image_file.filename:
        return publish_result_page(
            title="Загрузка картинки не удалась",
            message="Файл не выбран.",
            detail="Вернись к черновику и выбери изображение.",
            is_error=True,
        )
    suffix = Path(image_file.filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        return publish_result_page(
            title="Загрузка картинки не удалась",
            message="Поддерживаются PNG, JPG и WEBP.",
            is_error=True,
        )
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(Path(image_file.filename).stem)}-{uuid4().hex[:8]}{suffix}"
    path = settings.media_dir / filename
    path.write_bytes(await image_file.read())
    agent.storage.update_draft_content(draft_id, content)
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=str(path),
        prompt="manual upload",
        source="upload",
    )
    return render_draft_page(draft_id, draft["platform"], content)


@app.get("/drafts/{draft_id}/image/delete")
def delete_draft_image_get(draft_id: int) -> RedirectResponse:
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)


@app.post("/drafts/{draft_id}/image/delete", response_class=HTMLResponse)
async def delete_draft_image(
    draft_id: int,
    content: str = Form(""),
    image_asset_id: int | None = Form(None),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    current_content = content or draft["content"]
    agent.storage.update_draft_content(draft_id, current_content)
    latest_image = agent.storage.get_latest_media_asset(draft_id)
    target_asset_id = image_asset_id or (latest_image["id"] if latest_image else None)
    deleted = (
        agent.storage.delete_media_asset(target_asset_id, draft_id)
        if target_asset_id is not None
        else None
    )
    if deleted:
        try:
            path = Path(deleted["path"]).resolve()
            if path.is_relative_to(settings.media_dir.resolve()) and path.exists():
                path.unlink()
        except OSError:
            pass
    return render_draft_page(draft_id, draft["platform"], current_content)


@app.post("/drafts/{draft_id}/media/{asset_id}/select", response_class=HTMLResponse)
async def select_draft_media(
    draft_id: int,
    asset_id: int,
    content: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    current_content = content or draft["content"]
    agent.storage.update_draft_content(draft_id, current_content)
    if not agent.storage.mark_media_asset_current(asset_id, draft_id):
        raise HTTPException(status_code=404, detail="Медиа не найдено")
    return render_draft_page(draft_id, draft["platform"], current_content)


@app.post("/drafts/{draft_id}/blog")
async def publish_draft_to_blog(
    draft_id: int,
    content: str = Form(...),
    blog_kind: str = Form("article"),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_content = clean_article_text(content)
    kind = blog_kind if blog_kind in {"article", "project", "wiki"} else "article"
    agent.storage.update_draft_content(draft_id, clean_content, "published")
    _, path = create_blog_post_from_draft(
        draft_id,
        draft,
        clean_content,
        blog_kind=kind,
        demo_url=demo_url,
        trial_limit=trial_limit,
    )
    return RedirectResponse(path, status_code=303)


def render_draft_page(draft_id: int, platform: str, content: str) -> str:
    cleaned_content = clean_article_text(content)
    if cleaned_content != content:
        agent.storage.update_draft_content(draft_id, cleaned_content)
        content = cleaned_content
    escaped_content = escape(content)
    active_style = agent.storage.get_setting("active_style", "base") or "base"
    style_options = "\n".join(
        f'<option value="{escape(style["id"])}" {"selected" if style["id"] == active_style else ""}>{escape(style["title"])}</option>'
        for style in list_styles(settings)
    )
    image = agent.storage.get_latest_media_asset(draft_id)
    image_assets = agent.storage.list_media_assets(draft_id=draft_id, kind="image", limit=12)
    video_assets = agent.storage.list_media_assets(draft_id=draft_id, kind="video", limit=6)
    variants = agent.storage.list_draft_variants(draft_id)
    compare_variants = agent.storage.list_draft_compare_variants(draft_id, limit=9)
    research_reports = agent.storage.list_research_reports(draft_id, limit=3)
    task_notes = agent.storage.list_task_notes(draft_id=draft_id, limit=12)
    history = agent.storage.list_draft_history(draft_id, limit=10)
    variant_sections = "\n".join(
        f"""
        <details class="panel" {"open" if destination == platform else ""}>
          <summary><strong>{escape(platform_label(destination))}</strong></summary>
          <textarea name="content_{destination}" style="min-height: 220px;">{escape(variants.get(destination, ""))}</textarea>
        </details>
        """
        for destination in DESTINATION_PLATFORMS
        if variants.get(destination)
    )
    image_src = media_url(image["path"], settings) if image else None
    image_block = (
        f"""
        <p><img src="{escape(image_src)}" alt="Картинка черновика" style="max-width: 100%; border-radius: 8px; border: 1px solid #e5e5e5;"></p>
        <input type="hidden" name="image_asset_id" value="{image['id']}">
        <p><small>Активная картинка #{image['id']} · {escape(image.get('source') or '')}</small></p>
        """
        if image_src
        else "<p><small>Картинка пока не добавлена.</small></p>"
    )
    image_gallery = "\n".join(
        f"""
        <article class="blog-card">
          <img src="{escape(media_url(asset['path'], settings) or '')}" alt="Вариант обложки" style="width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:8px; border:1px solid #e0d8ca;">
          <p><small>{escape(asset.get('source') or '')} · #{asset['id']}</small></p>
          <button type="submit" formaction="/drafts/{draft_id}/media/{asset['id']}/select">Сделать активной</button>
        </article>
        """
        for asset in image_assets
        if media_url(asset["path"], settings)
    )
    video_gallery = "\n".join(
        f"""
        <article class="blog-card">
          <p><span class="pill">video</span> <small>{escape(asset.get('source') or '')} · #{asset['id']}</small></p>
          <p><a href="{escape(asset['path'])}" target="_blank" rel="noopener">{escape(asset['path'])}</a></p>
          <p><small>{escape(asset.get('prompt') or '')[:180]}</small></p>
        </article>
        """
        for asset in video_assets
    )
    history_block = render_draft_history(draft_id, history)
    compare_block = render_draft_compare_variants(draft_id, compare_variants)
    research_block = render_draft_research_reports(draft_id, research_reports)
    notes_block = render_draft_task_notes(draft_id, task_notes)
    body = f"""
        <section class="panel">
        <form method="post" action="/drafts/{draft_id}/publish" enctype="multipart/form-data">
          <input type="hidden" name="platform" value="{escape(platform)}">
          <textarea name="content">{escaped_content}</textarea>
          <p><small>Для Telegram с картинкой публикация отправляется одним фото-сообщением: первая строка остаётся заголовком, весь текст идёт в подпись к картинке. Лимит Telegram для такой подписи — 1024 символа.</small></p>
          <div class="panel">
            <h2>Рерайт</h2>
            <label>Активный стиль</label>
            <select name="style_id" disabled>{style_options}</select>
            <p><small>Сменить активный стиль можно на странице <a href="/admin/styles">«Стили»</a>.</small></p>
            <label for="rewrite_instructions">Задача на рерайт</label>
            <textarea id="rewrite_instructions" name="rewrite_instructions" style="min-height: 120px;" placeholder="Например: сделай живее, добавь личный опыт, убери канцелярит, сократи для Telegram, сделай сильный хук..."></textarea>
          </div>
          <div class="panel">
            <h2>Картинка</h2>
            {image_block}
            <label for="image_prompt">Сгенерировать по теме</label>
            <input id="image_prompt" name="image_prompt" type="text" placeholder="Например: разработчик собирает AI-агента, редакционный стиль, без текста и логотипов">
            <label for="image_file">Загрузить картинку</label>
            <input id="image_file" name="image_file" type="file" accept="image/png,image/jpeg,image/webp">
            <h3>Медиатека черновика</h3>
            <div class="blog-grid">{image_gallery or "<p><small>Других вариантов обложки пока нет.</small></p>"}</div>
            <h3>Видео-анонсы</h3>
            <div class="blog-grid">{video_gallery or "<p><small>Видео пока не создано.</small></p>"}</div>
          </div>
          <div class="panel">
            <h2>Куда отправить</h2>
            <p><small>Стиль черновика: {escape(platform_label(platform))}. Отметь площадки, куда отправить именно эту версию текста.</small></p>
            <label><input type="checkbox" name="destinations" value="telegram" {"checked" if platform == "telegram" else ""}> Telegram</label>
            <label><input type="checkbox" name="destinations" value="max" {"checked" if platform == "max" else ""}> MAX</label>
            <label><input type="checkbox" name="destinations" value="vk" {"checked" if platform == "vk" else ""}> ВКонтакте</label>
            <label><input type="checkbox" name="destinations" value="vc" {"checked" if platform == "vc" else ""}> VC — подготовить</label>
            <label><input type="checkbox" name="destinations" value="dzen" {"checked" if platform == "dzen" else ""}> Дзен — подготовить</label>
            <label><input type="checkbox" name="destinations" value="blog" {"checked" if platform == "blog" else ""}> Наш блог — статья</label>
            <label><input type="checkbox" name="destinations" value="blog_project" {"checked" if platform == "blog_project" else ""}> Наш сайт — проект с демо</label>
            <label><input type="checkbox" name="destinations" value="wiki" {"checked" if platform == "wiki" else ""}> Wiki — база знаний</label>
            <label>Demo URL для проекта</label>
            <input name="demo_url" placeholder="https://...">
            <label>Лимит проб</label>
            <input type="number" name="trial_limit" min="1" max="20" value="5">
            <p><small>Для VC/Дзена агент сохранит готовую версию в журнале. Для Telegram/VK отправит сразу. Для блога создаст публичную страницу.</small></p>
          </div>
          <div class="panel">
            <h2>Версии под площадки</h2>
            <p><small>Нажми «Сделать версии под площадки», агент создаст отдельный текст для каждой площадки. Перед отправкой можно править каждый вариант прямо здесь.</small></p>
            {variant_sections or "<p><small>Версии ещё не созданы.</small></p>"}
          </div>
          <br>
          <div class="actions">
            <button type="submit" formaction="/drafts/{draft_id}/rewrite">Рерайт в моём стиле</button>
            <button type="submit" formaction="/drafts/{draft_id}/compare">AI Compare</button>
            <button type="submit" formaction="/drafts/{draft_id}/research">Research Report</button>
            <button type="submit" formaction="/drafts/{draft_id}/variants">Сделать версии под площадки</button>
            <button type="submit" formaction="/drafts/{draft_id}/image/generate">Сгенерировать картинку</button>
            <button type="submit" formaction="/drafts/{draft_id}/image/generate-batch">4 варианта обложки</button>
            <button type="submit" formaction="/drafts/{draft_id}/video/generate">Сделать видео-анонс</button>
            <button type="submit" formaction="/drafts/{draft_id}/growth">Telegram growth brief</button>
            <button type="submit" formaction="/drafts/{draft_id}/image/upload">Загрузить картинку</button>
            <button type="submit" formmethod="post" formaction="/drafts/{draft_id}/image/delete" {"disabled" if not image else ""}>Удалить картинку</button>
            <label style="margin:0;">
              Отложить
              <input type="datetime-local" name="scheduled_at">
            </label>
            <button type="submit" formaction="/drafts/{draft_id}/schedule">Запланировать</button>
            <button type="submit" formaction="/drafts/{draft_id}/blog" name="blog_kind" value="article">Создать статью в блоге</button>
            <button type="submit" formaction="/drafts/{draft_id}/blog" name="blog_kind" value="project">Создать проект</button>
            <button type="submit" formaction="/drafts/{draft_id}/blog" name="blog_kind" value="wiki">Создать Wiki</button>
            <button type="submit" formaction="/drafts/{draft_id}/publish/multi">Опубликовать выбранное</button>
            <button type="submit">Опубликовать / отметить готовым</button>
          </div>
        </form>
        </section>
        {research_block}
        {compare_block}
        {notes_block}
        {history_block}
    """
    return page_shell(f"Черновик #{draft_id}", body, f"Площадка: {platform_label(platform)}. Рерайт, картинка, публикация или отложенный выпуск.")


def render_draft_compare_variants(draft_id: int, variants: list[dict]) -> str:
    cards = "\n".join(render_draft_compare_card(draft_id, variant) for variant in variants)
    return f"""
      <section class="panel">
        <h2>AI Compare</h2>
        <p><small>Швейцарский нож для рерайта: несколько компактных вариантов, один клик — применить лучший. Перед применением текущий текст сохраняется в истории.</small></p>
        <div class="blog-grid">{cards or '<p><small>Вариантов пока нет. Нажми «AI Compare» в панели действий черновика.</small></p>'}</div>
      </section>
    """


def render_draft_research_reports(draft_id: int, reports: list[dict]) -> str:
    cards = "\n".join(render_draft_research_card(report) for report in reports)
    return f"""
      <section class="panel">
        <h2>Research Report</h2>
        <p><small>Компактный ресёрч перед статьёй: источник, углы подачи, риски, вопросы для проверки. Создаётся кнопкой `Research Report` в панели действий.</small></p>
        <form class="toolbar" method="post" action="/drafts/{draft_id}/research">
          <input type="hidden" name="content" value="">
          <input name="research_question" placeholder="Дополнительный вопрос для проверки, необязательно">
          <button type="submit">Быстрый отчёт</button>
        </form>
        <div class="blog-grid">{cards or '<p><small>Отчётов пока нет.</small></p>'}</div>
      </section>
    """


def render_draft_research_card(report: dict) -> str:
    preview = clean_article_text(report.get("content") or "")
    if len(preview) > 720:
        preview = preview[:720].rstrip() + "..."
    return f"""
      <article class="blog-card">
        <h3>{escape(report.get('title') or 'Research Report')}</h3>
        <p><small>{escape(report.get('created_at') or '')}</small></p>
        <pre style="white-space:pre-wrap; max-height:360px; overflow:auto;">{escape(preview)}</pre>
      </article>
    """


def render_draft_task_notes(draft_id: int, notes: list[dict]) -> str:
    rows = "\n".join(render_task_note_row(note) for note in notes)
    return f"""
      <section class="panel">
        <h2>Task Notes</h2>
        <p><small>Маленькие follow-up задачи по материалу: проверить источник, добрать картинку, вернуться завтра, сделать короткий пост.</small></p>
        <form class="toolbar" method="post" action="/drafts/{draft_id}/notes">
          <input name="note_title" placeholder="Что сделать">
          <input name="note_content" placeholder="Детали">
          <input type="datetime-local" name="due_at">
          <button type="submit">Добавить</button>
        </form>
        <table>
          <thead><tr><th>Статус</th><th>Задача</th><th>Срок</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Заметок пока нет.</td></tr>'}</tbody>
        </table>
      </section>
    """


def render_task_note_row(note: dict) -> str:
    draft_id = note.get("draft_id")
    return f"""
      <tr>
        <td><span class="pill">{escape(status_label(note.get('status') or 'open'))}</span></td>
        <td><strong>{escape(note.get('title') or '')}</strong><br><small>{escape(note.get('content') or '')}</small></td>
        <td>{escape(note.get('due_at') or '')}</td>
        <td>
          <div class="actions">
            <form method="post" action="/admin/task-notes/{note['id']}/status">
              <input type="hidden" name="status" value="done">
              <input type="hidden" name="return_to" value="{f'/drafts/{draft_id}' if draft_id else '/admin/task-notes'}">
              <button type="submit">Готово</button>
            </form>
            <form method="post" action="/admin/task-notes/{note['id']}/delete">
              <input type="hidden" name="return_to" value="{f'/drafts/{draft_id}' if draft_id else '/admin/task-notes'}">
              <button type="submit">Удалить</button>
            </form>
          </div>
        </td>
      </tr>
    """


def render_draft_compare_card(draft_id: int, variant: dict) -> str:
    content = clean_article_text(variant.get("content") or "")
    preview = re.sub(r"\s+", " ", content).strip()
    if len(preview) > 520:
        preview = preview[:520].rstrip() + "..."
    selected = '<span class="pill">выбран</span>' if int(variant.get("selected") or 0) else ""
    return f"""
      <article class="blog-card">
        <h3>{escape(variant.get('label') or 'Вариант')} {selected}</h3>
        <p><small>{escape(variant.get('provider') or '')} · {escape(variant.get('model') or '')}</small></p>
        <p>{escape(preview)}</p>
        <p><small>{escape(variant.get('note') or '')}</small></p>
        <form method="post" action="/drafts/{draft_id}/compare/{variant['id']}/apply">
          <button type="submit">Применить</button>
        </form>
      </article>
    """


def render_draft_history(draft_id: int, history: list[dict]) -> str:
    rows = "\n".join(render_draft_history_row(draft_id, revision) for revision in history)
    return f"""
      <section class="panel">
        <h2>История и откат</h2>
        <p><small>Перед каждым рерайтом агент сохраняет текущий текст. Если новая версия не понравилась, можно восстановить предыдущую.</small></p>
        <table>
          <thead><tr><th>Когда</th><th>Версия</th><th>Превью</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Истории пока нет.</td></tr>'}</tbody>
        </table>
      </section>
    """


def render_draft_history_row(draft_id: int, revision: dict) -> str:
    preview = re.sub(r"\s+", " ", clean_article_text(revision.get("content") or "")).strip()
    if len(preview) > 220:
        preview = preview[:220].rstrip() + "..."
    return f"""
      <tr>
        <td>{escape(revision.get('created_at') or '')}</td>
        <td><strong>{escape(revision.get('note') or 'Версия')}</strong><br><small>#{revision['id']}</small></td>
        <td>{escape(preview)}</td>
        <td>
          <form method="post" action="/drafts/{draft_id}/history/{revision['id']}/restore">
            <button type="submit">Откатиться</button>
          </form>
        </td>
      </tr>
    """


@app.post("/drafts/{draft_id}/schedule")
async def schedule_draft_publication(
    draft_id: int,
    platform: str = Form(...),
    content: str = Form(...),
    scheduled_at: str = Form(...),
    destinations: Annotated[list[str] | None, Form()] = None,
    request: Request = None,
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    form = await request.form() if request else {}
    selected = selected_destinations_from_form(form, destinations, platform)
    if not selected:
        return HTMLResponse(
            publish_result_page(
                title="Планирование не удалось",
                message="Выбери хотя бы одну площадку в блоке «Куда отправить».",
                is_error=True,
            ),
            status_code=400,
        )
    try:
        scheduled_dt = date_parser.parse(scheduled_at).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return HTMLResponse(
            publish_result_page(
                title="Планирование не удалось",
                message="Не получилось разобрать дату публикации.",
                detail="Выбери дату и время в поле «Отложить».",
                is_error=True,
            ),
            status_code=400,
        )
    image = agent.storage.get_latest_media_asset(draft_id)
    image_path = image["path"] if image else None
    form_content = {key: str(value) for key, value in form.items() if key.startswith("content_")}
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content, "scheduled")
    queue_ids = []
    for destination in selected:
        queue_ids.append(
            agent.storage.schedule_publication(
                draft_id=draft_id,
                item_id=draft["item_id"],
                platform=destination,
                content=destination_content(form_content, destination, clean_content),
                scheduled_at=scheduled_dt.isoformat(),
                image_path=image_path,
            )
        )
    return HTMLResponse(
        publish_result_page(
            title="Запланировано",
            message=f"Публикации поставлены в очередь: {', '.join(f'#{item}' for item in queue_ids)}.",
            detail=f"Площадки: {', '.join(platform_label(item) for item in selected)}. UTC: {scheduled_dt.isoformat()}",
        )
    )


@app.post("/drafts/{draft_id}/publish")
async def publish_draft(
    draft_id: int, platform: str = Form(...), content: str = Form(...)
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    if platform in {"blog", "blog_project", "wiki"}:
        kind = {"blog": "article", "blog_project": "project", "wiki": "wiki"}[platform]
        clean_content = clean_article_text(content)
        agent.storage.update_draft_content(draft_id, clean_content, "published")
        _, path = create_blog_post_from_draft(draft_id, draft, clean_content, blog_kind=kind)
        agent.storage.save_publication(
            draft_id=draft_id,
            item_id=draft["item_id"],
            platform=platform,
            status="published",
            content=clean_content,
            response={"status": "published", "path": path},
            image_path=(agent.storage.get_latest_media_asset(draft_id) or {}).get("path"),
        )
        return HTMLResponse(
            publish_result_page(
                title="Страница создана",
                message=f"Площадка: {platform_label(platform)}",
                detail=f'<a href="{escape(path)}">{escape(path)}</a>',
            )
        )
    try:
        image = agent.storage.get_latest_media_asset(draft_id)
        image_path = image["path"] if image else None
        result = await publish(
            platform,
            content,
            settings,
            image_path=image_path,
            overrides=publish_overrides(),
        )
    except PublishError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Публикация не удалась",
                message=str(exc),
                detail="Проверь токены, права бота и ID площадки.",
                is_error=True,
            ),
            status_code=400,
        )
    agent.storage.update_draft_content(
        draft_id,
        content,
        "published" if platform in {"telegram", "max", "vk"} else "ready",
    )
    agent.storage.save_publication(
        draft_id=draft_id,
        item_id=draft["item_id"],
        platform=platform,
        status="published" if platform in {"telegram", "max", "vk"} else "ready",
        content=content,
        response=result,
        image_path=image_path,
    )
    message = "Опубликовано" if platform in {"telegram", "max", "vk"} else "Черновик готов"
    detail = result.get("message", "") if isinstance(result, dict) else ""
    return HTMLResponse(
        publish_result_page(title=message, message=f"Площадка: {platform_label(platform)}", detail=detail)
    )


@app.post("/drafts/{draft_id}/publish/multi")
async def publish_draft_multi(
    draft_id: int,
    platform: str = Form(...),
    content: str = Form(...),
    destinations: Annotated[list[str] | None, Form()] = None,
    blog_kind: str = Form("article"),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
    request: Request = None,
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    form = await request.form() if request else {}
    form_content = {key: str(value) for key, value in form.items() if key.startswith("content_")}
    selected = selected_destinations_from_form(form, destinations, platform)
    if not selected:
        return HTMLResponse(
            publish_result_page(
                title="Нечего публиковать",
                message="Выбери хотя бы одну площадку в блоке «Куда отправить».",
                is_error=True,
            ),
            status_code=400,
        )

    image = agent.storage.get_latest_media_asset(draft_id)
    image_path = image["path"] if image else None
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content, "published")
    results: list[str] = []
    errors: list[str] = []

    for destination in selected:
        if destination in {"blog", "blog_project", "wiki"}:
            target_content = destination_content(form_content, destination, clean_content)
            kind = {"blog": "article", "blog_project": "project", "wiki": "wiki"}[destination]
            _, path = create_blog_post_from_draft(
                draft_id,
                draft,
                target_content,
                blog_kind=kind,
                demo_url=demo_url,
                trial_limit=trial_limit,
            )
            result = {"status": "published", "path": path}
            agent.storage.save_publication(
                draft_id=draft_id,
                item_id=draft["item_id"],
                platform=destination,
                status="published",
                content=target_content,
                response=result,
                image_path=image_path,
            )
            results.append(f"{platform_label(destination)}: создано <a href=\"{escape(path)}\">{escape(path)}</a>")
            continue
        try:
            target_content = destination_content(form_content, destination, clean_content)
            result = await publish(
                destination,
                target_content,
                settings,
                image_path=image_path,
                overrides=publish_overrides(),
            )
        except PublishError as exc:
            errors.append(f"{platform_label(destination)}: {escape(str(exc))}")
            continue
        status = "published" if destination in {"telegram", "max", "vk"} else "ready"
        agent.storage.save_publication(
            draft_id=draft_id,
            item_id=draft["item_id"],
            platform=destination,
            status=status,
            content=target_content,
            response=result,
            image_path=image_path,
        )
        results.append(f"{platform_label(destination)}: {'опубликовано' if status == 'published' else 'готово'}")

    status_title = "Готово" if not errors else "Частично готово"
    body = f"""
      <section class="panel">
        <h2>{status_title}</h2>
        <p><span class="pill">Выбрано: {escape(', '.join(platform_label(item) for item in selected))}</span></p>
        <h3>Успешно</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in results) or '<li>Нет успешных действий.</li>'}</ul>
        <h3>Ошибки</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in errors) or '<li>Ошибок нет.</li>'}</ul>
        <div class="actions"><a class="btn" href="/drafts/{draft_id}">Вернуться к черновику</a></div>
      </section>
    """
    return HTMLResponse(page_shell(status_title, body, "Публикация по выбранным площадкам."))


@app.get("/drafts/{draft_id}/publish")
def publish_get_redirect(draft_id: int) -> RedirectResponse:
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)


@app.get("/admin/publications", response_class=HTMLResponse)
@app.get("/publications", response_class=HTMLResponse)
def publication_log() -> str:
    publications = agent.storage.list_publications(limit=200)
    rows = "\n".join(
        f"""
        <tr>
          <td>{escape(pub['published_at'])}</td>
          <td>{escape(platform_label(pub['platform']))}</td>
          <td>{escape(status_label(pub['status']))}</td>
          <td><strong>{escape(pub['item_title'])}</strong><br><small>черновик #{pub['draft_id']} · внешний ID {escape(pub['external_id'] or '—')}</small></td>
          <td>{publication_link(pub)}</td>
        </tr>
        """
        for pub in publications
    )
    body = f"""
        <section class="panel">
        <table>
          <thead><tr><th>Когда</th><th>Площадка</th><th>Статус</th><th>Материал</th><th>Ссылка</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        </section>
    """
    return page_shell("Журнал публикаций", body, "База размещений: когда, куда и какой материал был опубликован.")


@app.get("/admin/media", response_class=HTMLResponse)
@app.get("/media-library", response_class=HTMLResponse)
def media_library() -> str:
    assets = agent.storage.list_media_assets(limit=200)
    cards = "\n".join(render_media_asset_card(asset) for asset in assets)
    body = f"""
        <section class="panel">
          <h2>Медиатека</h2>
          <p><small>Все обложки, загруженные изображения и видео-анонсы. Активная обложка черновика — самый свежий image-asset у этого черновика.</small></p>
          <div class="blog-grid">{cards or "<p>Медиа пока нет.</p>"}</div>
        </section>
    """
    return page_shell("Медиатека", body, "Картинки, варианты обложек и видео-анонсы для публикаций.")


def render_media_asset_card(asset: dict) -> str:
    title = escape(asset.get("item_title") or f"Черновик #{asset['draft_id']}")
    source = escape(asset.get("source") or "")
    prompt = escape(asset.get("prompt") or "")[:220]
    if asset.get("kind") == "video":
        preview = f'<p><span class="pill">video</span></p><p><a href="{escape(asset["path"])}" target="_blank" rel="noopener">Открыть видео</a></p>'
    else:
        src = media_url(asset.get("path"), settings)
        preview = (
            f'<img src="{escape(src)}" alt="{title}" style="width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:8px; border:1px solid #e0d8ca;">'
            if src
            else f'<p><a href="{escape(asset.get("path") or "")}" target="_blank" rel="noopener">Открыть файл</a></p>'
        )
    return f"""
      <article class="blog-card">
        {preview}
        <h3>{title}</h3>
        <p><span class="pill">{escape(asset.get('kind') or 'image')}</span> <small>{source} · #{asset['id']}</small></p>
        <p><small>{prompt}</small></p>
        <div class="actions"><a class="btn" href="/drafts/{asset['draft_id']}">Черновик #{asset['draft_id']}</a></div>
      </article>
    """


def publication_link(pub: dict) -> str:
    if pub.get("external_url"):
        return f'<a href="{escape(pub["external_url"])}" target="_blank">опубликовано</a>'
    return f'<a href="{escape(pub["source_url"])}" target="_blank">источник</a>'


def publish_result_page(title: str, message: str, detail: str = "", is_error: bool = False) -> str:
    color = "#b42318" if is_error else "#067647"
    body = f"""
        <section class="panel">
          <h2 style="color: {color}; margin-top: 0;">{escape(title)}</h2>
          <p>{escape(message)}</p>
          <p>{escape(detail)}</p>
          <p><a class="btn" href="/admin/topics">Вернуться к темам</a></p>
        </section>
    """
    return page_shell(title, body)
