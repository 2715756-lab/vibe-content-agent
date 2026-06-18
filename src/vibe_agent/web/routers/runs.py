# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.post("/admin/run")
@router.post("/run")
async def run_collection() -> RedirectResponse:
    await run_search_with_progress()
    return RedirectResponse("/admin/control", status_code=303)


@router.post("/admin/run/start")
@router.post("/run/start")
async def start_collection() -> dict:
    if not search_state["running"]:
        asyncio.create_task(run_search_with_progress())
    return search_state


@router.get("/admin/run/status")
@router.get("/run/status")
def run_status() -> dict:
    return search_state


@router.post("/admin/viral/start")
@router.post("/viral/start")
async def start_viral_research() -> dict:
    if not viral_state["running"]:
        asyncio.create_task(run_viral_research_with_progress())
    return viral_state


@router.get("/admin/viral/status")
@router.get("/viral/status")
def viral_status() -> dict:
    return viral_state


@router.get("/admin/schedule", response_class=HTMLResponse)
@router.get("/schedule", response_class=HTMLResponse)
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


@router.post("/admin/schedule/search")
@router.post("/schedule/search")
async def update_search_schedule(hour: int = Form(...), minute: int = Form(...)) -> RedirectResponse:
    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    agent.storage.set_setting("daily_run_hour", str(hour))
    agent.storage.set_setting("daily_run_minute", str(minute))
    if scheduler.running:
        install_search_job()
    return RedirectResponse("/admin/schedule", status_code=303)


