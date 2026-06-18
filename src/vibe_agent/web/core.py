# AUTO-SPLIT from monolithic api.py. Shared layer: imports, globals, constants, helpers.
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
studio_state = {
    "running": False,
    "progress": 0,
    "stage": "ожидание",
    "mode": "",
    "run_id": None,
    "draft_id": None,
    "item_id": None,
    "ideas": [],
    "readiness": {},
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


settings.media_dir.mkdir(parents=True, exist_ok=True)

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
    "custom_image_api_key",
    "custom_image_base_url",
    "custom_image_model",
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
            "custom_image_api_key",
            "custom_image_base_url",
            "custom_image_model",
            "custom_image_notes",
            "polza_api_key",
            "polza_model",
            "polza_aspect_ratio",
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
.studio-shell { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: 18px; align-items: start; }
.studio-command {
  position: sticky;
  top: 18px;
  background: #111827;
  color: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 18px 45px rgba(17, 24, 39, 0.22);
}
.studio-command h2 { margin: 0 0 10px; font-size: 26px; }
.studio-command p { color: #d7f5ef; line-height: 1.5; }
.studio-command label { color: #f8f4ec; }
.studio-command input, .studio-command select, .studio-command textarea {
  width: 100%;
  background: rgba(255,255,255,0.96);
}
.studio-submit { width: 100%; margin-top: 14px; text-align: center; }
.studio-main { display: grid; gap: 14px; }
.studio-card {
  background: #fff;
  border: 1px solid #e0d8ca;
  border-radius: 8px;
  padding: 18px;
  box-shadow: 0 10px 28px rgba(43, 34, 25, 0.06);
}
.studio-progress {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 18px;
  align-items: center;
}
.eyebrow {
  margin: 0 0 8px;
  color: #2a9d8f;
  font-size: 12px;
  font-weight: 850;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.check-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 8px; }
.check-grid label {
  margin: 0;
  padding: 9px;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 8px;
  background: rgba(255,255,255,0.08);
  font-size: 14px;
}
.readiness-bar {
  height: 12px;
  border-radius: 999px;
  background: #eee6da;
  overflow: hidden;
  margin: 12px 0;
}
.readiness-bar span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #2a9d8f, #e9c46a, #e76f51);
}
.result-card h2 { margin-bottom: 8px; }
@media (max-width: 900px) {
  .page { padding: 18px; }
  .hero { display: block; }
  .nav { margin-top: 16px; }
  .grid-form { grid-template-columns: 1fr; }
  .engagement { grid-template-columns: 1fr; }
  .control-shell { grid-template-columns: 1fr; }
  .control-rail { position: static; }
  .control-card, .control-card.wide { grid-column: 1 / -1; }
  .studio-shell { grid-template-columns: 1fr; }
  .studio-command { position: static; }
  .studio-progress { grid-template-columns: 1fr; }
  .check-grid { grid-template-columns: 1fr; }
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
              <a class="btn primary" href="/admin/studio">Студия</a>
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










def studio_status_payload() -> dict:
    payload = dict(studio_state)
    payload["ideas"] = payload.get("ideas", [])[:5] if not payload.get("running") else []
    payload["details"] = studio_status_details()
    return payload


def studio_status_details() -> str:
    if studio_state.get("error"):
        return str(studio_state["error"])
    if studio_state.get("draft_id"):
        score = studio_state.get("readiness", {}).get("score")
        if score is not None:
            return f"Черновик #{studio_state['draft_id']} готов. Готовность: {score}%."
        return f"Черновик #{studio_state['draft_id']} создан, пакет ещё собирается."
    if studio_state.get("running"):
        return f"Run #{studio_state.get('run_id')} выполняется."
    return "Запусти сборку, чтобы получить публикационный пакет."


async def execute_publication_studio(
    run_id: int,
    mode: str,
    topic: str,
    tone: str,
    destinations: list[str],
    make_research: bool,
    make_variants: bool,
    make_image: bool,
    make_compare: bool,
) -> None:
    selected_item: dict | None = None
    draft_id: int | None = None
    warnings: list[str] = []
    try:
        update_studio_state(8, "ищу и обновляю источники")
        scout_step = agent.storage.create_agent_step(
            run_id, "Source Scout", "search_only", "Собираю источники и свежие темы."
        )
        try:
            result = await asyncio.wait_for(agent.collect_and_rank(), timeout=90)
            agent.storage.finish_agent_step(
                scout_step,
                "success",
                f"Источники обновлены: найдено {result.get('fetched', 0)}, новых {result.get('inserted', 0)}.",
                result,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Сбор источников: {exc}")
            agent.storage.finish_agent_step(
                scout_step,
                "warning",
                "Не все источники обновились, продолжаю по сохранённой базе.",
                {"error": str(exc)},
            )

        update_studio_state(22, "ранжирую темы и ищу угол")
        trend_step = agent.storage.create_agent_step(
            run_id, "Trend Analyst", "compute_only", "Выбираю тему и угол подачи."
        )
        candidates = select_studio_candidates(topic, mode)
        if not candidates:
            agent.storage.finish_agent_step(
                trend_step,
                "failed",
                "Не нашёл подходящих тем. Добавь источник или задай более широкую тему.",
                {"topic": topic, "mode": mode},
            )
            raise RuntimeError("Не нашёл подходящих тем для студии.")
        ideas = [studio_idea_from_item(item, mode, topic) for item in candidates[:5]]
        selected_item = candidates[0]
        studio_state.update({"item_id": selected_item["id"], "ideas": ideas})
        agent.storage.finish_agent_step(
            trend_step,
            "success",
            f"Выбрана тема: {selected_item['title']}",
            {"ideas": ideas, "destinations": destinations},
        )

        update_studio_state(36, "пишу основу материала")
        draft_step = agent.storage.create_agent_step(
            run_id, "Style Writer", "draft_only", "Создаю базовый черновик."
        )
        platform = "telegram" if mode == "fast_post" else "blog"
        try:
            draft = await asyncio.wait_for(agent.draft(selected_item["id"], platform), timeout=120)
            draft_id = int(draft["draft_id"])
            content = clean_article_text(draft["content"])
            agent.storage.finish_agent_step(
                draft_step,
                "success",
                f"Базовый черновик создан: #{draft_id}.",
                {"draft_id": draft_id, "platform": platform},
            )
        except Exception as exc:  # noqa: BLE001
            content = fallback_studio_draft(selected_item, mode, topic)
            draft_id = agent.storage.save_draft(selected_item["id"], platform, content)
            warnings.append(f"AI draft fallback: {exc}")
            agent.storage.finish_agent_step(
                draft_step,
                "warning",
                f"AI-черновик не сработал, создан fallback #{draft_id}.",
                {"draft_id": draft_id, "error": str(exc)},
            )
        studio_state.update({"draft_id": draft_id})

        update_studio_state(50, "довожу текст до готовой статьи")
        rewrite_step = agent.storage.create_agent_step(
            run_id, "Rewrite Editor", "draft_only", "Делаю рерайт под выбранный режим и тон."
        )
        rewrite_instructions = studio_rewrite_instructions(mode, topic, tone)
        agent.storage.save_draft_revision(draft_id, content, "Перед студийным рерайтом")
        try:
            rewritten = await asyncio.wait_for(
                agent.rewrite(
                    draft_id,
                    content,
                    style_text=active_style_text(),
                    rewrite_instructions=rewrite_instructions,
                ),
                timeout=120,
            )
            content = clean_article_text(rewritten["content"])
            agent.storage.finish_agent_step(
                rewrite_step,
                "success",
                "Текст переписан в финальный черновик без служебных фраз.",
                {"length": len(content), "tone": tone},
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Рерайт: {exc}")
            agent.storage.finish_agent_step(
                rewrite_step,
                "warning",
                "Рерайт не сработал, оставлен базовый черновик.",
                {"error": str(exc)},
            )

        if make_research:
            update_studio_state(62, "собираю research report")
            research_step = agent.storage.create_agent_step(
                run_id, "Fact Pack", "read_only", "Фиксирую источники, риски и углы подачи."
            )
            report = build_research_report(
                {"content": content},
                selected_item,
                content,
                "Проверить, почему эта тема может дать читателю практический результат уже сейчас.",
            )
            agent.storage.add_research_report(
                draft_id,
                selected_item["id"],
                f"Studio Research: {selected_item['title']}",
                report,
            )
            agent.storage.finish_agent_step(
                research_step,
                "success",
                "Research report добавлен к черновику.",
                {"source": selected_item.get("url"), "title": selected_item.get("title")},
            )

        if make_variants:
            update_studio_state(74, "делаю версии под площадки")
            variant_step = agent.storage.create_agent_step(
                run_id, "Platform Packager", "draft_only", "Создаю версии для выбранных площадок."
            )
            created_variants = []
            for destination in destinations:
                try:
                    await asyncio.wait_for(
                        agent.generate_variant(
                            draft_id,
                            destination,
                            content,
                            style_text=active_style_text(),
                        ),
                        timeout=90,
                    )
                    created_variants.append(destination)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Версия {destination}: {exc}")
            agent.storage.finish_agent_step(
                variant_step,
                "success" if created_variants else "warning",
                f"Созданы версии: {', '.join(platform_label(item) for item in created_variants) or 'нет'}.",
                {"created": created_variants, "requested": destinations},
            )

        if make_compare:
            update_studio_state(82, "готовлю варианты захода")
            compare_step = agent.storage.create_agent_step(
                run_id, "Hook Lab", "draft_only", "Генерирую варианты сильного захода."
            )
            try:
                await asyncio.wait_for(
                    agent.compare_rewrites(
                        draft_id,
                        content,
                        style_text=active_style_text(),
                        rewrite_instructions="Сделай 3 версии с разными хуками. Без markdown-разметки и служебных фраз.",
                        limit=3,
                    ),
                    timeout=120,
                )
                agent.storage.finish_agent_step(
                    compare_step,
                    "success",
                    "AI Compare добавил варианты захода.",
                    {"limit": 3},
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"AI Compare: {exc}")
                agent.storage.finish_agent_step(
                    compare_step,
                    "warning",
                    "AI Compare не сработал.",
                    {"error": str(exc)},
                )

        if make_image:
            update_studio_state(88, "генерирую обложку")
            image_step = agent.storage.create_agent_step(
                run_id, "Cover Maker", "draft_only", "Генерирую обложку по теме."
            )
            image_prompt = studio_image_prompt(selected_item, mode)
            try:
                path, prompt, source = await asyncio.wait_for(
                    generate_image_for_topic(
                        selected_item["title"],
                        selected_item.get("summary", ""),
                        image_prompt,
                        settings,
                        image_config=image_generation_config(),
                    ),
                    timeout=120,
                )
                agent.storage.save_media_asset(
                    draft_id=draft_id,
                    item_id=selected_item["id"],
                    path=str(path),
                    prompt=prompt,
                    source=source,
                )
                agent.storage.finish_agent_step(
                    image_step,
                    "success",
                    "Обложка добавлена к черновику.",
                    {"source": source, "path": str(path)},
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Картинка: {exc}")
                agent.storage.finish_agent_step(
                    image_step,
                    "warning",
                    "Картинка не сгенерировалась, материал оставлен без обложки.",
                    {"error": str(exc)},
                )

        update_studio_state(96, "считаю готовность")
        readiness = compute_studio_readiness(draft_id, selected_item, destinations, make_research)
        studio_state.update({"readiness": readiness})
        qa_step = agent.storage.create_agent_step(
            run_id, "Readiness QA", "verification", "Проверяю готовность публикационного пакета."
        )
        agent.storage.add_task_note(
            "Одобрить студийный пакет",
            "Проверить текст, research report, обложку и нажать «Опубликовать выбранное».",
            draft_id=draft_id,
            item_id=selected_item["id"],
        )
        agent.storage.finish_agent_step(
            qa_step,
            "success" if readiness["score"] >= 75 else "warning",
            f"Готовность: {readiness['score']}%.",
            readiness,
        )
        agent.storage.finish_agent_run(
            run_id,
            "warning" if warnings else "success",
            f"Студия собрала публикационный пакет: черновик #{draft_id}, готовность {readiness['score']}%.",
            error="; ".join(warnings[:4]),
            item_id=selected_item["id"],
            draft_id=draft_id,
        )
        update_studio_state(100, "готово", running=False)
    except Exception as exc:  # noqa: BLE001
        studio_state.update({"error": str(exc)})
        agent.storage.finish_agent_run(
            run_id,
            "failed",
            "Студия остановилась на ошибке.",
            error=str(exc),
            item_id=selected_item["id"] if selected_item else None,
            draft_id=draft_id,
        )
        update_studio_state(100, "ошибка", running=False)


def update_studio_state(progress: int, stage: str, running: bool = True) -> None:
    studio_state.update(
        {
            "running": running,
            "progress": max(0, min(100, progress)),
            "stage": stage,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def select_studio_candidates(topic: str, mode: str) -> list[dict]:
    query = topic.strip()
    if query:
        candidates = agent.storage.list_items(limit=20, query=query)
        if candidates:
            return sorted(candidates, key=lambda item: item.get("score") or 0, reverse=True)
    if mode == "viral":
        ideas = rank_viral_ideas(agent.storage.list_items(limit=80, query=query or None))
        id_order = [idea["item_id"] for idea in ideas]
        items = {item["id"]: item for item in agent.storage.list_items(limit=120)}
        ranked = [items[item_id] for item_id in id_order if item_id in items]
        if ranked:
            return ranked
    candidates = agent.storage.list_editorial_candidates(limit=30)
    if candidates:
        return candidates
    return agent.storage.list_items(limit=30, query=query or None)


def studio_idea_from_item(item: dict, mode: str, topic: str) -> dict:
    idea = viral_idea_from_item(item)
    idea["mode"] = mode
    idea["topic_match"] = bool(topic and topic.lower() in f"{item.get('title', '')} {item.get('summary', '')}".lower())
    return idea


def studio_rewrite_instructions(mode: str, topic: str, tone: str) -> str:
    mode_rules = {
        "ai_news": "Сделай готовую статью по AI/dev новости: хук, суть, почему важно, практический вывод, вопрос к аудитории.",
        "viral": "Найди конфликт и востребованный угол. Сделай материал цепким, но без кликбейта и без недоказанных утверждений.",
        "github": "Если тема про GitHub или инструмент, объясни что это, кому полезно, как попробовать, где ограничения.",
        "project": "Пиши как личную историю создания проекта: боль, решение, что пошло не по плану, результат, следующий шаг.",
        "fast_post": "Сделай короткий готовый пост для Telegram/VK до 1000 символов, с сильной первой строкой.",
    }
    tone_rules = {
        "author": "Тон: живой, личный, практичный, в стиле автора.",
        "bold": "Тон: смелее, больше позиции, конфликта и ясного авторского вывода.",
        "calm": "Тон: спокойно, экспертно, без хайпа.",
        "telegram": "Тон: коротко, разговорно, плотная мысль, без длинных вступлений.",
    }
    topic_rule = f"Фокус темы: {topic.strip()}." if topic.strip() else "Фокус темы выбери по самому сильному источнику."
    return (
        f"{mode_rules.get(mode, mode_rules['ai_news'])}\n"
        f"{tone_rules.get(tone, tone_rules['author'])}\n"
        f"{topic_rule}\n"
        "Верни только готовый текст публикации. Без markdown-звёздочек, решёток, служебных фраз, <think>, объяснений и подписи 'черновик'. "
        "Заголовки делай обычными строками, текст удобочитаемым."
    )


def studio_image_prompt(item: dict, mode: str) -> str:
    title = item.get("title") or "AI editorial agent"
    mode_hint = {
        "project": "personal product-building story, developer workspace, tangible progress",
        "github": "open-source software project, code repository, practical developer tool",
        "viral": "high-energy editorial cover, technology trend, clear focal object",
        "fast_post": "clean social media cover, one strong idea, minimal composition",
    }.get(mode, "AI news editorial cover, practical technology, modern workspace")
    return (
        f"{mode_hint}. Topic: {title}. High-quality editorial image, 16:9, no text, no logos, "
        "realistic or premium product illustration, clear subject, not abstract."
    )


def fallback_studio_draft(item: dict, mode: str, topic: str) -> str:
    title = item.get("title") or topic.strip() or "Новая тема про ИИ"
    summary = item.get("summary") or "Описание источника пока короткое, нужна ручная проверка."
    source = item.get("url") or ""
    return clean_article_text(
        f"""
        {title}

        Я бы взял эту тему в работу, потому что она находится на пересечении ИИ, разработки и практического результата.

        Суть
        {summary}

        Почему это важно
        Сейчас аудитории всё меньше интересны абстрактные разговоры про ИИ. Людям нужен понятный результат: что можно собрать, ускорить, проверить или внедрить уже сейчас.

        Мой вывод
        Эту тему стоит раскрыть не как пересказ новости, а как практический разбор: что внутри, кому полезно, где ограничения и как попробовать.

        Источник
        {source}
        """
    )


def compute_studio_readiness(
    draft_id: int,
    item: dict,
    destinations: list[str],
    research_requested: bool,
) -> dict:
    draft = agent.storage.get_draft(draft_id) or {}
    content = clean_article_text(draft.get("content") or "")
    variants = agent.storage.list_draft_variants(draft_id)
    image = agent.storage.get_latest_media_asset(draft_id)
    reports = agent.storage.list_research_reports(draft_id, limit=1)
    checks = {
        "text": min(25, int(len(content) / 80)),
        "source": 20 if item.get("url") else 5,
        "variants": 20 if all(destination in variants for destination in destinations) else min(20, len(variants) * 5),
        "image": 20 if image else 0,
        "research": 15 if reports or not research_requested else 0,
    }
    score = max(0, min(100, sum(checks.values())))
    return {
        "score": score,
        "checks": checks,
        "destinations": destinations,
        "has_image": bool(image),
        "has_research": bool(reports),
        "text_length": len(content),
    }


def render_studio_item(item: dict) -> str:
    return f"""
      <article>
        <h3>{escape(item.get('title') or '')}</h3>
        <p>{escape(item.get('source') or '')} · score {escape(str(round(float(item.get('score') or 0), 1)))}</p>
        <form method="post" action="/admin/studio/start">
          <input type="hidden" name="mode" value="ai_news">
          <input type="hidden" name="topic" value="{escape(item.get('title') or '')}">
          <input type="hidden" name="tone" value="author">
          <input type="hidden" name="destinations" value="telegram">
          <input type="hidden" name="destinations" value="blog">
          <input type="hidden" name="make_research" value="1">
          <input type="hidden" name="make_variants" value="1">
          <input type="hidden" name="make_image" value="1">
          <button type="submit">Собрать из этой темы</button>
        </form>
      </article>
    """


def render_studio_result(run: dict) -> str:
    if not run:
        return ""
    draft_id = run.get("draft_id")
    readiness = compute_studio_readiness(
        draft_id,
        agent.storage.get_item(run["item_id"]) if run.get("item_id") else {},
        ["telegram", "blog"],
        True,
    ) if draft_id else {"score": 0, "checks": {}}
    steps = agent.storage.list_agent_steps(run["id"])
    return f"""
      <section class="studio-card result-card">
        <p class="eyebrow">последний пакет</p>
        <h2>{escape(run.get('item_title') or 'Публикационный пакет')}</h2>
        <div class="readiness-bar"><span style="width:{readiness['score']}%"></span></div>
        <p><strong>Готовность: {readiness['score']}%</strong> · {escape(status_label(run.get('status') or ''))}</p>
        <p>{escape(run.get('summary') or run.get('objective') or '')}</p>
        {render_run_pipeline(steps)}
        <div class="actions">
          {f'<a class="btn primary" href="/drafts/{draft_id}">Открыть пакет #{draft_id}</a>' if draft_id else ''}
          <a class="btn" href="/admin/editorial/runs/{run['id']}">Trace</a>
        </div>
      </section>
    """


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






def source_link(source: dict) -> str:
    if source.get("type") == "apify_actor":
        actor_id = source.get("actor_id") or source.get("url") or ""
        actor_url = f"https://apify.com/{actor_id}" if "/" in actor_id else "https://apify.com/store"
        query = f"<br><small>query: {escape(source.get('query') or '—')}</small>" if source.get("query") else ""
        max_items = f"<br><small>max: {escape(str(source.get('max_items') or '—'))}</small>"
        return f'<a href="{escape(actor_url)}" target="_blank" rel="noopener">{escape(actor_id)}</a>{query}{max_items}'
    url = source.get("url", "")
    return f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(url)}</a>'










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






def select_option(value: str, label: str, current: str) -> str:
    selected = " selected" if value == current else ""
    return f'<option value="{escape(value)}"{selected}>{escape(label)}</option>'
















def make_post_slug_by_id(post_id: int) -> str:
    post = agent.storage.get_blog_post(post_id)
    return post["slug"] if post else ""









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
