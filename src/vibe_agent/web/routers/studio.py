# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/studio", response_class=HTMLResponse)
def publication_studio() -> str:
    items = agent.storage.list_items(limit=6)
    drafts = agent.storage.list_drafts()[:5]
    studio_runs = [run for run in agent.storage.list_agent_runs(limit=20) if run.get("kind") == "studio"][:5]
    latest_result = render_studio_result(studio_runs[0]) if studio_runs else ""
    modes = [
        ("ai_news", "AI новости", "Свежая новость с источниками и практическим выводом."),
        ("viral", "Вирусная тема", "Выбирает тему по сигналам спроса, конфликта и обсуждаемости."),
        ("github", "GitHub обзор", "Упаковывает репозиторий или инструмент в статью/пост."),
        ("project", "Мой проект", "Превращает личный vibe-coding опыт в историю."),
        ("fast_post", "Быстрый пост", "Короткий Telegram/VK материал без длинной статьи."),
    ]
    mode_options = "\n".join(
        f'<option value="{value}">{escape(label)} — {escape(hint)}</option>' for value, label, hint in modes
    )
    body = f"""
      <section class="studio-shell">
        <aside class="studio-command">
          <p class="eyebrow">одно окно результата</p>
          <h2>Что выпускаем сегодня?</h2>
          <p>Выбери направление, задай тему или оставь поле пустым. Агент соберёт источники, выберет угол, сделает черновик, версии под площадки, research report и обложку.</p>
          <form id="studioForm" class="studio-form" method="post" action="/admin/studio/start">
            <label>Режим</label>
            <select name="mode">{mode_options}</select>
            <label>Тема, URL или направление</label>
            <textarea name="topic" style="min-height: 120px;" placeholder="Например: AI-агенты для малого бизнеса, новый GitHub-репозиторий, мой путь создания агента, новости Codex..."></textarea>
            <label>Тон результата</label>
            <select name="tone">
              <option value="author">В моём стиле, живо и практично</option>
              <option value="bold">Смелее, больше конфликта и позиции</option>
              <option value="calm">Спокойно, экспертно, без хайпа</option>
              <option value="telegram">Коротко, как сильный Telegram-пост</option>
            </select>
            <label>Площадки</label>
            <div class="check-grid">
              <label><input type="checkbox" name="destinations" value="telegram" checked> Telegram</label>
              <label><input type="checkbox" name="destinations" value="blog" checked> Блог</label>
              <label><input type="checkbox" name="destinations" value="vk"> VK</label>
              <label><input type="checkbox" name="destinations" value="vc"> VC</label>
              <label><input type="checkbox" name="destinations" value="dzen"> Дзен</label>
            </div>
            <label>Что собрать</label>
            <div class="check-grid">
              <label><input type="checkbox" name="make_research" value="1" checked> Research</label>
              <label><input type="checkbox" name="make_variants" value="1" checked> Версии</label>
              <label><input type="checkbox" name="make_image" value="1" checked> Картинка</label>
              <label><input type="checkbox" name="make_compare" value="1"> 3 варианта хука</label>
            </div>
            <button class="primary studio-submit" type="submit">Собрать готовый материал</button>
          </form>
        </aside>
        <div class="studio-main">
          <section class="studio-card">
            <div class="studio-progress">
              <div id="studioClock" class="clock-progress" style="--progress: {studio_state['progress']}%; --deg: {float(studio_state['progress']) * 3.6};"><span id="studioProgress">{studio_state['progress']}%</span></div>
              <div>
                <p class="eyebrow">статус сборки</p>
                <h2 id="studioStage">{escape(studio_state.get('stage') or 'ожидание')}</h2>
                <p id="studioDetails">{escape(studio_status_details())}</p>
                <div class="actions">
                  <a id="studioDraftLink" class="btn primary" href="{f'/drafts/{studio_state["draft_id"]}' if studio_state.get("draft_id") else '#'}" {'style="display:none;"' if not studio_state.get("draft_id") else ''}>Открыть результат</a>
                  <a class="btn" href="/admin/editorial">Журнал прогонов</a>
                </div>
              </div>
            </div>
          </section>
          {latest_result}
          <section class="studio-card">
            <h2>Свежие темы для старта</h2>
            <div class="control-list">{''.join(render_studio_item(item) for item in items) or '<p>Тем пока нет. Запусти сбор.</p>'}</div>
          </section>
          <section class="studio-card">
            <h2>Последние черновики</h2>
            <div class="control-list">{''.join(render_control_draft(draft) for draft in drafts) or '<p>Черновиков пока нет.</p>'}</div>
          </section>
        </div>
      </section>
      <script>
        const studioForm = document.getElementById('studioForm');
        function setStudioClock(progress) {{
          const value = Number(progress || 0);
          const clock = document.getElementById('studioClock');
          clock.style.setProperty('--progress', `${{value}}%`);
          clock.style.setProperty('--deg', String(value * 3.6));
          document.getElementById('studioProgress').textContent = `${{value}}%`;
        }}
        async function pollStudio() {{
          const response = await fetch('/admin/studio/status');
          const state = await response.json();
          setStudioClock(state.progress);
          document.getElementById('studioStage').textContent = state.stage || 'ожидание';
          document.getElementById('studioDetails').textContent = state.error || state.details || '';
          const link = document.getElementById('studioDraftLink');
          if (state.draft_id) {{
            link.href = `/drafts/${{state.draft_id}}`;
            link.style.display = '';
          }}
          const button = studioForm ? studioForm.querySelector('button[type="submit"]') : null;
          if (button) {{
            button.disabled = Boolean(state.running);
            button.textContent = state.running ? 'Собираю материал...' : 'Собрать готовый материал';
          }}
          if (state.running) setTimeout(pollStudio, 1200);
        }}
        if (studioForm) {{
          studioForm.addEventListener('submit', async (event) => {{
            event.preventDefault();
            await fetch('/admin/studio/start', {{ method: 'POST', body: new FormData(studioForm) }});
            pollStudio();
          }});
        }}
        pollStudio();
      </script>
    """
    return page_shell("Студия публикации", body, "Тема → источники → рерайт → картинка → версии → одобрение.")


@router.post("/admin/studio/start")
async def start_publication_studio(
    mode: str = Form("ai_news"),
    topic: str = Form(""),
    tone: str = Form("author"),
    destinations: Annotated[list[str] | None, Form()] = None,
    make_research: str = Form(""),
    make_variants: str = Form(""),
    make_image: str = Form(""),
    make_compare: str = Form(""),
) -> dict:
    if studio_state["running"]:
        return studio_status_payload()
    selected = [item for item in (destinations or []) if item in DESTINATION_PLATFORMS]
    if not selected:
        selected = ["telegram", "blog"]
    objective = f"Studio: {mode}; topic={topic.strip() or 'auto'}; destinations={','.join(selected)}"
    run_id = agent.storage.create_agent_run("studio", objective)
    studio_state.update(
        {
            "running": True,
            "progress": 3,
            "stage": "ставлю задачу",
            "mode": mode,
            "run_id": run_id,
            "draft_id": None,
            "item_id": None,
            "ideas": [],
            "readiness": {},
            "error": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    asyncio.create_task(
        execute_publication_studio(
            run_id,
            mode=mode,
            topic=topic,
            tone=tone,
            destinations=selected,
            make_research=bool(make_research),
            make_variants=bool(make_variants),
            make_image=bool(make_image),
            make_compare=bool(make_compare),
        )
    )
    return studio_status_payload()


@router.get("/admin/studio/status")
def studio_status() -> dict:
    return studio_status_payload()


