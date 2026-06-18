# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin")
def admin_home() -> RedirectResponse:
    return RedirectResponse("/admin/control", status_code=303)


@router.get("/admin/help", response_class=HTMLResponse)
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


@router.get("/admin/control", response_class=HTMLResponse)
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


@router.get("/admin/topics", response_class=HTMLResponse)
def index(q: str = "") -> str:
    items = agent.storage.list_items(limit=50, query=q.strip() or None)
    viral_ideas_html = render_viral_cards(viral_state.get("ideas", []))
    apify_pill = '<span class="pill">Apify</span>'
    rows = "\n".join(
        f"""
        <tr>
          <td><span class="pill">{item['score']:.2f}</span></td>
          <td>
            <strong>{escape(item['title'])}</strong>
            {apify_pill if str(item.get("source") or "").startswith("Apify:") else ""}
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


@router.get("/admin/server", response_class=HTMLResponse)
@router.get("/server", response_class=HTMLResponse)
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


@router.get("/admin/publications", response_class=HTMLResponse)
@router.get("/publications", response_class=HTMLResponse)
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


@router.get("/admin/media", response_class=HTMLResponse)
@router.get("/media-library", response_class=HTMLResponse)
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


