# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/apify/results", response_class=HTMLResponse)
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


@router.post("/admin/apify/run")
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


@router.get("/admin/osint", response_class=HTMLResponse)
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


@router.post("/admin/osint/run")
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


@router.get("/admin/settings", response_class=HTMLResponse)
@router.get("/settings", response_class=HTMLResponse)
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
            <option value="custom_image" {"selected" if ai_image_provider == "custom_image" else ""}>Custom Image (OpenAI-совместимый)</option>
            <option value="muapi_images" {"selected" if ai_image_provider == "muapi_images" else ""}>MuAPI Images / Video — 200+ моделей</option>
            <option value="cloudflare_worker_images" {"selected" if ai_image_provider == "cloudflare_worker_images" else ""}>Cloudflare Worker Images — бесплатно/быстро</option>
            <option value="openai_images" {"selected" if ai_image_provider == "openai_images" else ""}>OpenAI Images</option>
            <option value="polza_images" {"selected" if ai_image_provider == "polza_images" else ""}>Polza.AI — Яндекс ART, дешево (2.91₽)</option>
            <option value="custom_notes" {"selected" if ai_image_provider == "custom_notes" else ""}>Custom / внешний генератор</option>
          </select>
          <div id="custom_image_settings" style="display:{'block' if ai_image_provider == 'custom_image' else 'none'}">
            <label>Custom Image API key</label>
            <input type="password" name="custom_image_api_key" placeholder="{'уже задано' if saved_setting('custom_image_api_key') else 'API key'}" value="{escape(saved_setting('custom_image_api_key', ''))}">
            <label>Custom Image base URL</label>
            <input name="custom_image_base_url" value="{escape(saved_setting('custom_image_base_url', 'https://inference-api.nousresearch.com/v1'))}">
            <label>Custom Image model</label>
            <input name="custom_image_model" value="{escape(saved_setting('custom_image_model', 'google/gemini-3-pro-image'))}">
          </div>
          <div id="polza_settings" style="display:{'block' if ai_image_provider == 'polza_images' else 'none'}">
            <label>Polza.AI API key</label>
            <input type="password" name="polza_api_key" placeholder="{'уже задано' if saved_setting('polza_api_key') else 'pza_...'}" value="{escape(saved_setting('polza_api_key', ''))}">
            <label>Polza.AI model</label>
            <input name="polza_model" value="{escape(saved_setting('polza_model', 'yandex/yandex-art'))}">
            <label>Aspect ratio</label>
            <select name="polza_aspect_ratio">
              <option value="16:9" {"selected" if saved_setting('polza_aspect_ratio', '16:9') == '16:9' else ''}>16:9 — обложка статьи</option>
              <option value="1:1" {"selected" if saved_setting('polza_aspect_ratio') == '1:1' else ''}>1:1 — квадрат</option>
              <option value="4:5" {"selected" if saved_setting('polza_aspect_ratio') == '4:5' else ''}>4:5 — соцсети</option>
              <option value="9:16" {"selected" if saved_setting('polza_aspect_ratio') == '9:16' else ''}>9:16 — сторис</option>
            </select>
            <p class="hint">Polza.AI — 2.91₽ за картинку, Яндекс ART. API ключ: <code>pza_...</code></p>
          </div>
          <script>
          (function() {{
            var sel = document.querySelector('select[name="ai_image_provider"]');
            function toggle() {{
              var v = sel.value;
              document.getElementById('custom_image_settings').style.display = v == 'custom_image' ? 'block' : 'none';
              document.getElementById('polza_settings').style.display = v == 'polza_images' ? 'block' : 'none';
            }}
            sel?.addEventListener('change', toggle);
            toggle();
          }})();
          </script>
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


@router.post("/admin/settings/publish")
@router.post("/settings/publish")
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


@router.post("/admin/max/test")
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


@router.post("/admin/settings/apify")
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


@router.post("/admin/apify/test")
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


@router.post("/admin/settings/ai")
@router.post("/settings/ai")
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
    custom_image_api_key: str = Form(""),
    custom_image_base_url: str = Form(""),
    custom_image_model: str = Form(""),
    custom_image_notes: str = Form(""),
    polza_api_key: str = Form(""),
    polza_model: str = Form("yandex/yandex-art"),
    polza_aspect_ratio: str = Form("16:9"),
) -> RedirectResponse:
    text_provider = ai_text_provider if ai_text_provider in {"openrouter", "gemini", "openai", "custom"} else "openrouter"
    image_provider = (
        ai_image_provider
        if ai_image_provider
        in {"fallback", "openrouter_images", "custom_image", "muapi_images", "cloudflare_worker_images", "openai_images", "custom_notes", "polza_images"}
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
        "custom_image_base_url": custom_image_base_url.strip(),
        "custom_image_model": custom_image_model.strip(),
        "custom_image_notes": custom_image_notes.strip(),
        "polza_model": polza_model.strip() or "yandex/yandex-art",
        "polza_aspect_ratio": polza_aspect_ratio.strip() or "16:9",
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
        "custom_image_api_key": custom_image_api_key,
        "polza_api_key": polza_api_key,
    }
    for key, value in secret_values.items():
        if value.strip():
            agent.storage.set_setting(key, value.strip())
    return RedirectResponse("/admin/settings", status_code=303)


