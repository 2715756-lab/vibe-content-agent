# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/blog", response_class=HTMLResponse)
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


@router.get("/projects", response_class=HTMLResponse)
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


@router.get("/wiki", response_class=HTMLResponse)
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


@router.get("/blog/{slug}", response_class=HTMLResponse)
def view_blog_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/blog", "article", request)


@router.get("/projects/{slug}", response_class=HTMLResponse)
def view_project_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/projects", "project", request)


@router.get("/wiki/{slug}", response_class=HTMLResponse)
def view_wiki_post(
    slug: str, request: Request, visitor_id: str | None = Cookie(None)
) -> Response:
    return render_blog_post_response(slug, visitor_id, "/wiki", "wiki", request)


@router.post("/blog/{slug}/react")
async def react_blog_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/blog", "article")


@router.post("/projects/{slug}/react")
async def react_project_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/projects", "project")


@router.post("/wiki/{slug}/react")
async def react_wiki_post(
    slug: str, reaction: str = Form(...), visitor_id: str | None = Cookie(None)
) -> Response:
    return await react_to_post_response(slug, reaction, visitor_id, "/wiki", "wiki")


@router.post("/blog/{slug}/comment")
async def comment_blog_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/blog", "article")


@router.post("/projects/{slug}/comment")
async def comment_project_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/projects", "project")


@router.post("/wiki/{slug}/comment")
async def comment_wiki_post(
    slug: str,
    author: str = Form(""),
    content: str = Form(...),
    visitor_id: str | None = Cookie(None),
) -> Response:
    return await comment_post_response(slug, author, content, visitor_id, "/wiki", "wiki")


@router.post("/blog/{slug}/try")
async def try_blog_project(slug: str, visitor_id: str | None = Cookie(None)) -> Response:
    return await try_project_response(slug, visitor_id, "/blog")


@router.post("/projects/{slug}/try")
async def try_public_project(slug: str, visitor_id: str | None = Cookie(None)) -> Response:
    return await try_project_response(slug, visitor_id, "/projects")


