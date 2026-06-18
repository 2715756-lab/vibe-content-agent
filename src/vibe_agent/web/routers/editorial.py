# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/editorial", response_class=HTMLResponse)
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


@router.post("/admin/editorial/run")
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


@router.get("/admin/editorial/runs/{run_id}", response_class=HTMLResponse)
def view_editorial_run(run_id: int) -> str:
    return editorial_run_result(run_id)


