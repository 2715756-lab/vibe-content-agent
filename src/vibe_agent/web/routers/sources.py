# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/sources", response_class=HTMLResponse)
@router.get("/sources", response_class=HTMLResponse)
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


@router.post("/admin/sources")
@router.post("/sources")
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


@router.post("/admin/sources/preset/apify")
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


