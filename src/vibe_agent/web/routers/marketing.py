# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/indexnow-key.txt", response_class=PlainTextResponse)
def indexnow_key_file() -> str:
    key = saved_setting("indexnow_key")
    if not key:
        raise HTTPException(status_code=404, detail="IndexNow key не задан")
    return key


@router.get("/admin/seo", response_class=HTMLResponse)
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


@router.post("/admin/seo")
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


@router.post("/admin/seo/indexnow")
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


@router.get("/admin/marketing", response_class=HTMLResponse)
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


@router.get("/admin/growth", response_class=HTMLResponse)
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


@router.post("/admin/growth/links")
async def add_growth_link(
    name: str = Form(""),
    url: str = Form(""),
    source: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    if name.strip() and url.strip():
        agent.storage.add_growth_link(name.strip(), url.strip(), source.strip(), notes.strip())
    return RedirectResponse("/admin/growth", status_code=303)


@router.post("/admin/growth/links/{link_id}/delete")
async def delete_growth_link(link_id: int) -> RedirectResponse:
    agent.storage.delete_growth_link(link_id)
    return RedirectResponse("/admin/growth", status_code=303)


@router.post("/admin/growth/tests")
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


@router.post("/admin/growth/tests/{test_id}/delete")
async def delete_growth_test(test_id: int) -> RedirectResponse:
    agent.storage.delete_growth_test(test_id)
    return RedirectResponse("/admin/growth", status_code=303)


