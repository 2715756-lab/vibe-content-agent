import re
import asyncio
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from vibe_agent.config import Settings


PLATFORM_RULES = {
    "telegram": (
        "Коротко, разговорно, до 900 знаков. Первая строка — сильный заголовок. "
        "Это будет подпись к картинке в Telegram, поэтому заголовок и весь текст должны помещаться "
        "в один caption."
    ),
    "max": "Коротко и практично для MAX, до 2500 знаков, без кликбейта, с понятным CTA перейти в Telegram или на сайт.",
    "vk": "Дружелюбно и чуть подробнее, 1800-2500 знаков, с вопросом в конце.",
    "vc": "Структурно, аналитично, 4000-7000 знаков, с подзаголовками и выводами.",
    "dzen": "Понятно для широкой аудитории, 3000-5000 знаков, без перегруза терминами.",
    "blog": "Полноценная статья для собственного блога, 3000-6000 знаков, с сильным заголовком, подзаголовками и авторскими выводами.",
    "blog_project": "Описание проекта для собственного сайта: проблема, что делает инструмент, как попробовать, ограничения и следующий шаг.",
    "wiki": "Вечнозелёная wiki-заметка: спокойно, структурно, с определениями, практическими шагами, выводами и ссылками на связанные идеи. Без новостного хайпа.",
}

PLAIN_TEXT_FORMAT_RULES = (
    "Формат текста: plain text для публикации, не Markdown. "
    "Не используй решётки, звёздочки, жирный/курсив, горизонтальные линии, markdown-списки, "
    "служебные разделители, эмодзи как маркеры и техническую разметку. "
    "Заголовок — первая строка без спецсимволов. Подзаголовки — отдельные короткие строки "
    "без нумерации и без знаков #. После каждого заголовка и подзаголовка ставь пустую строку. "
    "Не называй разделы редакторскими словами вроде 'Хук', 'Лид', 'Секция', 'Блок', 'Тезисы'. "
    "Основной текст — живыми абзацами по 3-6 строк, удобно читать с телефона. "
    "Если нужен список, лучше перепиши его короткими абзацами с понятными переходами."
)

SERVICE_LINE_PATTERNS = [
    r"^черновик\s+для\s+.+$",
    r"^версия\s+в\s+стиле\s+автора\s+для\s+.+$",
    r"^задача\s+на\s+рерайт\s*:.*$",
    r"^дополнительная\s+задача\s+на\s+рерайт\s*:.*$",
    r"^[а-яёa-z][а-яёa-z\s.-]{1,60},\s*20\d{2}(?:[-‑]\d{2}){0,2}$",
    r"^</?(think|assistant|user|system)\s*>$",
]

INVALID_MODEL_RESPONSE_PATTERNS = [
    r"^\s*user\s+safety\s*:\s*(?:safe|unsafe)\s*$",
    r"^\s*safety\s+categories\s*:\s*.+$",
    r"^\s*blocked\s+reason\s*:\s*.+$",
    r"^\s*finish\s+reason\s*:\s*safety\s*$",
]

API_KEY_PATTERNS = [
    r"sk-or-[A-Za-z0-9._-]+",
    r"sk-[A-Za-z0-9._-]+",
]


def read_style_profile(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def text_model_config(settings: Settings, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    provider = overrides.get("ai_text_provider") or "openai"
    if provider == "openrouter":
        return {
            "provider": "openai_compatible",
            "api_key": normalize_api_key(overrides.get("openrouter_api_key"))
            or normalize_api_key(settings.openai_api_key),
            "model": overrides.get("openrouter_model") or "openrouter/auto",
            "base_url": overrides.get("openrouter_base_url") or "https://openrouter.ai/api/v1",
        }
    if provider == "gemini":
        return {
            "provider": "gemini",
            "api_key": normalize_api_key(overrides.get("gemini_api_key")),
            "model": overrides.get("gemini_model") or "gemini-flash-latest",
            "base_url": overrides.get("gemini_base_url")
            or "https://generativelanguage.googleapis.com/v1beta",
        }
    if provider == "custom":
        return {
            "provider": "openai_compatible",
            "api_key": normalize_api_key(overrides.get("custom_text_api_key"))
            or normalize_api_key(settings.openai_api_key),
            "model": overrides.get("custom_text_model") or settings.openai_model,
            "base_url": overrides.get("custom_text_base_url") or None,
        }
    return {
        "provider": "openai_compatible",
        "api_key": normalize_api_key(overrides.get("openai_api_key"))
        or normalize_api_key(settings.openai_api_key),
        "model": overrides.get("openai_model") or settings.openai_model,
        "base_url": None,
    }


def enabled(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def text_model_configs(settings: Settings, overrides: dict | None = None) -> list[dict]:
    overrides = overrides or {}
    configs: list[dict] = []
    primary = text_model_config(settings, overrides)
    if primary.get("api_key"):
        configs.append(primary)

    if enabled(overrides.get("text_fallback_enabled"), True):
        openrouter_key = normalize_api_key(overrides.get("openrouter_api_key")) or normalize_api_key(
            settings.openai_api_key
        )
        if openrouter_key:
            configs.append(
                {
                    "provider": "openai_compatible",
                    "api_key": openrouter_key,
                    "model": overrides.get("openrouter_free_model") or "openrouter/free",
                    "base_url": overrides.get("openrouter_base_url") or "https://openrouter.ai/api/v1",
                }
            )

        hf_key = normalize_api_key(overrides.get("huggingface_api_key"))
        hf_model = (overrides.get("huggingface_model") or "").strip()
        if hf_key and hf_model:
            configs.append(
                {
                    "provider": "openai_compatible",
                    "api_key": hf_key,
                    "model": hf_model,
                    "base_url": overrides.get("huggingface_base_url")
                    or "https://router.huggingface.co/v1",
                }
            )

    unique: list[dict] = []
    seen: set[tuple[str | None, str | None]] = set()
    for config in configs:
        identity = (config.get("base_url"), config.get("model"))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(config)
    return unique


def normalize_api_key(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    for pattern in API_KEY_PATTERNS:
        match = re.search(pattern, stripped)
        if match:
            return match.group(0)
    if stripped.isascii() and not re.search(r"\s", stripped):
        return stripped
    return None


def make_text_client(config: dict) -> AsyncOpenAI:
    if config.get("base_url"):
        return AsyncOpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=90)
    return AsyncOpenAI(api_key=config["api_key"], timeout=90)


async def complete_text(config: dict, messages: list[dict[str, str]], temperature: float) -> str:
    if config.get("provider") == "gemini":
        return await complete_text_gemini(config, messages, temperature)
    client = make_text_client(config)
    response = await client.chat.completions.create(
        model=config["model"],
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


async def complete_text_gemini(config: dict, messages: list[dict[str, str]], temperature: float) -> str:
    api_key = config.get("api_key")
    if not api_key:
        return ""
    base_url = (config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = config.get("model") or "gemini-flash-latest"
    system_text = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
    user_text = "\n\n".join(
        f"{message['role'].upper()}:\n{message['content']}"
        for message in messages
        if message["role"] != "system"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"{base_url}/models/{model}:generateContent",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()


def clean_article_text(content: str) -> str:
    content = strip_model_artifacts(content)
    lines: list[str] = []
    skip_factcheck = False
    for raw_line in content.replace("\r\n", "\n").split("\n"):
        raw_stripped = raw_line.strip()
        cleaned_line = strip_markdown_line(raw_line)
        line = cleaned_line.strip()
        lower = line.lower()
        if is_markdown_separator(line):
            continue
        if lower.startswith("что проверить перед публикацией"):
            skip_factcheck = True
            continue
        if skip_factcheck:
            if line and not raw_stripped.startswith(("-", "•", "*")):
                skip_factcheck = False
            else:
                continue
        if any(re.match(pattern, lower, flags=re.IGNORECASE) for pattern in SERVICE_LINE_PATTERNS):
            continue
        lines.append(cleaned_line.rstrip())

    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return remove_repeated_prefix(text)


def is_invalid_model_response(content: str) -> bool:
    text = clean_article_text(content)
    if not text:
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    safety_lines = 0
    for line in lines:
        if any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in INVALID_MODEL_RESPONSE_PATTERNS):
            safety_lines += 1
    if safety_lines and safety_lines == len(lines):
        return True
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return normalized in {"user safety: safe", "user safety: unsafe"}


def strip_markdown_line(line: str) -> str:
    value = line.rstrip()
    value = value.replace("\u2011", "-").replace("\u2010", "-")
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"^\s{0,3}>\s*", "", value)
    value = re.sub(r"^\s*[-*•]\s+", "", value)
    value = re.sub(r"^\s*\d{1,2}[.)]\s+(.{3,140})$", r"\1", value)
    without_editorial_prefix = re.sub(
        r"^\s*(хук|лид|секция|блок|тезисы)\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    if without_editorial_prefix != value and without_editorial_prefix:
        without_editorial_prefix = without_editorial_prefix[0].upper() + without_editorial_prefix[1:]
    value = without_editorial_prefix
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"__(.*?)__", r"\1", value)
    value = re.sub(r"(?<!\w)\*(.*?)\*(?!\w)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return value


def is_markdown_separator(line: str) -> bool:
    return bool(re.fullmatch(r"[-_*—\s]{3,}", line.strip()))


def strip_model_artifacts(content: str) -> str:
    text = content.replace("\r\n", "\n")
    first_close_think = re.search(r"</think>", text, flags=re.IGNORECASE)
    first_open_think = re.search(r"<think\b[^>]*>", text, flags=re.IGNORECASE)
    if first_close_think and (not first_open_think or first_close_think.start() < first_open_think.start()):
        text = text[first_close_think.end() :]
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?(?:think|assistant|user|system)\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:assistant|user|system)\s*:\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    return text


def remove_repeated_prefix(content: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    if len(paragraphs) < 2:
        return content.strip()
    cleaned: list[str] = []
    for paragraph in paragraphs:
        normalized = re.sub(r"\s+", " ", paragraph).strip().lower()
        if cleaned:
            previous = re.sub(r"\s+", " ", cleaned[-1]).strip().lower()
            if normalized == previous:
                continue
        cleaned.append(paragraph)
    return "\n\n".join(cleaned).strip()


def normalize_for_compare(content: str) -> str:
    return re.sub(r"\s+", " ", clean_article_text(content)).strip().lower()


def articles_are_same(left: str, right: str) -> bool:
    left_normalized = normalize_for_compare(left)
    right_normalized = normalize_for_compare(right)
    if not left_normalized or not right_normalized:
        return left_normalized == right_normalized
    if left_normalized == right_normalized:
        return True
    shorter = min(len(left_normalized), len(right_normalized))
    longer = max(len(left_normalized), len(right_normalized))
    return shorter / longer > 0.98 and (
        left_normalized in right_normalized or right_normalized in left_normalized
    )


def fallback_draft(item: dict, platform: str, settings: Settings) -> str:
    return f"""{item["title"]}

Суть: {item.get("summary") or "Нужно раскрыть суть новости после чтения источника."}

Почему это важно:
- тема связана с текущей волной AI-инструментов и разработки;
- можно проверить, есть ли практическая польза для личных проектов и вайбкодинга;
- хороший повод обсудить, где AI реально ускоряет работу, а где пока создаёт шум.

Мой вывод:
Я бы смотрел на это не как на очередную AI-новость, а как на сигнал: какие задачи скоро станет проще делать одному разработчику или небольшой команде.

Источник: {item["url"]}
"""


def fallback_rewrite(
    content: str,
    platform: str,
    settings: Settings,
    rewrite_instructions: str = "",
) -> str:
    return clean_article_text(content)


async def generate_draft(
    item: dict,
    platform: str,
    settings: Settings,
    ai_config: dict | None = None,
) -> str:
    configs = text_model_configs(settings, ai_config)
    if not configs:
        return fallback_draft(item, platform, settings)

    style = read_style_profile(settings.style_profile_path)
    for config in configs:
        try:
            content = await complete_text(
                config,
                [
                    {
                        "role": "system",
                        "content": (
                            "Ты креативный редактор и ghostwriter автора. "
                            "Пиши на русском. Не выдумывай факты, отделяй выводы от фактов. "
                            "Если источник или фрагменты на иностранном языке, переводи смысл на русский, "
                            "оставляя оригинальные названия продуктов, компаний, моделей и репозиториев. "
                            "Возвращай только готовый текст публикации: без служебных фраз, "
                            "без 'черновик', без 'версия', без пояснений о задаче. "
                            "Не выводи внутренние рассуждения, chain-of-thought, XML/HTML-теги, "
                            "теги <think>, <assistant>, <user> или markdown-обёртки ответа.\n\n"
                            f"{PLAIN_TEXT_FORMAT_RULES}\n\n"
                            f"Профиль стиля:\n{style}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Площадка: {platform}\n"
                            f"Правила площадки: {PLATFORM_RULES.get(platform, PLATFORM_RULES['telegram'])}\n"
                            f"{PLAIN_TEXT_FORMAT_RULES}\n"
                            f"Автор: {settings.author_name}\n\n"
                            f"Новость/тема:\nЗаголовок: {item['title']}\n"
                            f"Кратко: {item.get('summary', '')}\n"
                            f"Источник: {item['url']}\n\n"
                            "Сгенерируй готовую публикацию. Не называй её черновиком. "
                            "Если новость на английском или другом иностранном языке, сделай русский перевод "
                            "и адаптацию, а не вставляй иностранный текст как есть. "
                            "Не добавляй блоки 'что проверить', комментарии редактора, инструкции, "
                            "служебные заголовки или мета-текст. На выходе должен быть только текст статьи."
                            "Если модель хочет рассуждать — рассуждай скрыто, в ответ не выводи эти рассуждения."
                        ),
                    },
                ],
                temperature=0.75,
            )
            cleaned = clean_article_text(content)
            if is_invalid_model_response(cleaned):
                raise ValueError("model returned a safety/service response instead of article text")
            return cleaned
        except Exception:
            continue
    return fallback_draft(item, platform, settings)


async def rewrite_draft(
    content: str,
    platform: str,
    settings: Settings,
    style_text: str | None = None,
    rewrite_instructions: str = "",
    ai_config: dict | None = None,
) -> str:
    configs = text_model_configs(settings, ai_config)
    cleaned_content = clean_article_text(content)
    if not configs:
        return fallback_rewrite(cleaned_content, platform, settings, rewrite_instructions)

    style = style_text if style_text is not None else read_style_profile(settings.style_profile_path)

    async def request_rewrite(config: dict, strict: bool = False) -> str:
        task = rewrite_instructions.strip() or (
            "перепиши заметно в авторском стиле: живее, яснее, с другим хуком и другими формулировками"
        )
        strict_block = (
            "Предыдущая попытка вернула почти тот же текст. Сейчас обязательно измени структуру, "
            "хук, порядок абзацев и не менее 30% формулировок, сохранив факты. "
            if strict
            else ""
        )
        content = await complete_text(
            config,
            [
                {
                    "role": "system",
                    "content": (
                        "Ты редактор автора. Переписывай текст на русском в его стиле. "
                        "Сохраняй факты, не добавляй неподтвержденные детали. "
                        "Иностранные фрагменты переводи на русский, но сохраняй оригинальные названия "
                        "продуктов, компаний, моделей, команд и репозиториев. "
                        "На выходе возвращай только готовую статью без служебных вступлений. "
                        "Запрещено писать 'черновик для Telegram', 'версия в стиле автора', "
                        "'задача на рерайт' или любые пояснения о процессе. "
                        "Запрещены теги <think>, </think>, <assistant>, </assistant>, роли, XML/HTML, "
                        "служебные маркеры и внутренние рассуждения. "
                        "Запрещено возвращать исходный текст без заметных изменений. "
                        f"{PLAIN_TEXT_FORMAT_RULES}\n\n"
                        f"Профиль стиля:\n{style}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Площадка: {platform}\n"
                        f"Правила площадки: {PLATFORM_RULES.get(platform, PLATFORM_RULES['telegram'])}\n"
                        f"{PLAIN_TEXT_FORMAT_RULES}\n"
                        f"Обязательная задача на рерайт: {task}\n\n"
                        f"{strict_block}"
                        "Выполни именно эту задачу. Измени хук, ритм, лексику и структуру так, "
                        "чтобы результат явно отличался от исходника, но факты остались теми же. "
                        "Если задача просит научный стиль — сделай спокойнее, точнее, аналитичнее, "
                        "с терминами и причинно-следственными связками. "
                        "Если задача просит проще — убери сложные обороты. "
                        "Если задача просит короче — сократи. "
                        "Если задача просит эмоциональнее — добавь личную авторскую интонацию. "
                        "Если внутри есть повторяющиеся куски, служебные строки или старые обёртки "
                        "предыдущих рерайтов, удали их. Если внутри есть английский или другой "
                        "иностранный текст, переведи его на русский и адаптируй под русскоязычную аудиторию. "
                        "Не добавляй fact-check блок и не объясняй, что ты сделал. "
                        "Если модель хочет рассуждать — рассуждай скрыто, в ответ не выводи эти рассуждения. "
                        "Верни только финальный текст для публикации:\n\n"
                        f"{cleaned_content}"
                    ),
                },
            ],
            temperature=0.95 if strict else 0.85,
        )
        cleaned = clean_article_text(content)
        if is_invalid_model_response(cleaned):
            raise ValueError("model returned a safety/service response instead of rewritten text")
        return cleaned

    for config in configs:
        try:
            rewritten = await request_rewrite(config)
            if articles_are_same(cleaned_content, rewritten):
                rewritten = await request_rewrite(config, strict=True)
            # Safety / empty responses are not acceptable — keep trying next model
            if is_invalid_model_response(rewritten):
                continue
            if not articles_are_same(cleaned_content, rewritten):
                return clean_article_text(rewritten)
        except Exception:
            continue
    # All models failed — do NOT silently save bad content; return fallback
    result = fallback_rewrite(cleaned_content, platform, settings, rewrite_instructions)
    if is_invalid_model_response(result):
        # Fallback is also bad — return cleaned original rather than corrupt content
        return cleaned_content
    return result


async def rewrite_compare_candidates(
    content: str,
    platform: str,
    settings: Settings,
    style_text: str | None = None,
    rewrite_instructions: str = "",
    ai_config: dict | None = None,
    limit: int = 3,
) -> list[dict]:
    cleaned_content = clean_article_text(content)
    style = style_text if style_text is not None else read_style_profile(settings.style_profile_path)
    base_task = rewrite_instructions.strip() or (
        "перепиши в авторском стиле: живее, яснее, с сильным хуком и без служебного текста"
    )
    approaches = [
        ("Хук", "Сделай самый цепкий первый абзац, сильный заход и живую авторскую интонацию."),
        ("Аналитика", "Сделай спокойнее, точнее, глубже: больше причинно-следственных связок и ясной структуры."),
        ("Коротко", "Сожми, убери воду, сохрани смысл и сделай текст пригодным для быстрой публикации."),
    ]
    configs = text_model_configs(settings, ai_config)
    if not configs or str((ai_config or {}).get("ai_text_provider") or "").startswith("none"):
        return _fallback_compare_candidates(
            cleaned_content,
            platform,
            settings,
            base_task,
            approaches[: max(1, limit)],
        )
    candidates: list[dict] = []
    seen: set[str] = set()

    async def build_with_config(config: dict, label: str, approach: str) -> str:
        response = await complete_text(
            config,
            [
                {
                    "role": "system",
                    "content": (
                        "Ты редактор автора. Нужен один вариант текста для blind-compare. "
                        "Сохраняй факты, не добавляй неподтвержденные детали. "
                        "Возвращай только готовую статью на русском без markdown-звездочек, "
                        "служебных вступлений, ролей, XML/HTML, <think> и объяснений процесса. "
                        f"{PLAIN_TEXT_FORMAT_RULES}\n\n"
                        f"Профиль стиля:\n{style}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Площадка: {platform}\n"
                        f"Правила площадки: {PLATFORM_RULES.get(platform, PLATFORM_RULES['telegram'])}\n"
                        f"Общая задача: {base_task}\n"
                        f"Подход варианта «{label}»: {approach}\n\n"
                        "Сделай заметно новую версию: измени хук, порядок абзацев, лексику и ритм. "
                        "Не добавляй fact-check блок и не рассказывай, что ты сделал. "
                        "Верни только финальный текст:\n\n"
                        f"{cleaned_content}"
                    ),
                },
            ],
            temperature=0.9,
        )
        return clean_article_text(response)

    for index, (label, approach) in enumerate(approaches[: max(1, limit)]):
        config = configs[index % len(configs)] if configs else {}
        provider = config.get("provider", "fallback")
        model = config.get("model", "local-fallback")
        try:
            rewritten = await asyncio.wait_for(build_with_config(config, label, approach), timeout=25) if configs else ""
        except Exception:
            rewritten = ""
        if not rewritten or articles_are_same(cleaned_content, rewritten) or is_invalid_model_response(rewritten):
            rewritten = fallback_rewrite(
                cleaned_content,
                platform,
                settings,
                f"{base_task}. Подход: {approach}",
            )
            provider = "fallback"
            model = "local-fallback"
            # Fallback is also safety/corrupt — skip this candidate entirely
            if is_invalid_model_response(rewritten):
                continue
        fingerprint = re.sub(r"\s+", " ", rewritten).strip().lower()[:500]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append(
            {
                "label": label,
                "provider": provider,
                "model": model,
                "content": rewritten,
                "note": approach,
            }
        )
    return candidates


def _fallback_compare_candidates(
    content: str,
    platform: str,
    settings: Settings,
    base_task: str,
    approaches: list[tuple[str, str]],
) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()
    for index, (label, approach) in enumerate(approaches):
        rewritten = _fallback_compare_text(content, index)
        fingerprint = re.sub(r"\s+", " ", rewritten).strip().lower()[:500]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        candidates.append(
            {
                "label": label,
                "provider": "fallback",
                "model": "local-fallback",
                "content": rewritten,
                "note": approach,
            }
        )
    return candidates


def _fallback_compare_text(content: str, index: int) -> str:
    paragraphs = [item.strip() for item in clean_article_text(content).split("\n\n") if item.strip()]
    if not paragraphs:
        return ""
    title = paragraphs[0]
    body = paragraphs[1:] or [title]
    if index == 0:
        return clean_article_text(
            "\n\n".join(
                [
                    title,
                    "Если коротко: здесь есть рабочий сигнал, который стоит проверить на практике, а не просто пролистать как очередную AI-новость.",
                    *body,
                    "Мой вывод: такую тему стоит разбирать через пользу для реальных проектов, а не через громкость заголовка.",
                ]
            )
        )
    if index == 1:
        return clean_article_text(
            "\n\n".join(
                [
                    title,
                    "Что видно по фактам",
                    *body,
                    "Почему это важно",
                    "Такие истории показывают, куда движется рынок: меньше абстрактного хайпа, больше прикладных инструментов для одного автора, разработчика или небольшой команды.",
                ]
            )
        )
    return clean_article_text(
        "\n\n".join(
            [
                title,
                body[0],
                "Главное здесь не в громком названии, а в практическом вопросе: можно ли это применить в нашей AI-лаборатории, контенте или автоматизации.",
            ]
        )
    )
