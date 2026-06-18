# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/docs/telegram_growth_strategy.md", response_class=PlainTextResponse)
def telegram_growth_strategy_doc() -> str:
    path = Path("docs/telegram_growth_strategy.md")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Документ не найден")
    return path.read_text(encoding="utf-8")


@router.get("/docs/operator_help.md", response_class=PlainTextResponse)
def operator_help_doc() -> str:
    path = Path("docs/operator_help.md")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Документ не найден")
    return path.read_text(encoding="utf-8")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get(ZEN_VERIFICATION_PATH, response_class=HTMLResponse)
@router.get(ZEN_VERIFICATION_PATH_LOWER, response_class=HTMLResponse)
def zen_verification() -> str:
    if not ZEN_VERIFICATION_TOKEN:
        return '<meta name="zen-verification" content="" />'
    return f'<meta name="zen-verification" content="{ZEN_VERIFICATION_TOKEN}" />'


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt(request: Request) -> str:
    base = site_base_url(request)
    return "\n".join(
        [
            "User-agent: *",
            "Disallow: /admin/",
            "Disallow: /settings",
            "Disallow: /drafts/",
            "Disallow: /items/",
            "Disallow: /run",
            "Allow: /media/",
            f"Sitemap: {base}/sitemap.xml",
            f"Sitemap: {base}/rss.xml",
            "",
        ]
    )


@router.get("/sitemap.xml")
def sitemap_xml(request: Request) -> Response:
    urls = "\n".join(
        f"""
  <url>
    <loc>{escape(entry['loc'])}</loc>
    <lastmod>{escape(entry['lastmod'])}</lastmod>
    <changefreq>{'daily' if entry['loc'].endswith(('/blog', '/projects', '/wiki')) else 'weekly'}</changefreq>
    <priority>{escape(entry['priority'])}</priority>
  </url>"""
        for entry in public_url_entries(request)
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>
"""
    return Response(xml, media_type="application/xml; charset=utf-8")


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt(request: Request) -> str:
    lines = [
        f"# {seo_site_name()}",
        "",
        seo_default_description(),
        "",
        "## Основные разделы",
        f"- [Статьи]({absolute_site_url('/blog', request)}): материалы про ИИ, разработку, автоматизацию и вайбкодинг.",
        f"- [Проекты]({absolute_site_url('/projects', request)}): AI-инструменты и эксперименты, которые можно попробовать.",
        f"- [Wiki]({absolute_site_url('/wiki', request)}): база знаний по AI-проектам и рабочим практикам.",
        "",
        "## Последние материалы",
    ]
    for post in agent.storage.list_blog_posts(limit=100):
        path = blog_path_for_kind(post["kind"], post["slug"])
        lines.append(
            f"- [{post['title']}]({absolute_site_url(path, request)}): {meta_description(post.get('excerpt') or post.get('content'), 240)}"
        )
    lines.append("")
    return "\n".join(lines)


@router.get("/llms-full.txt", response_class=PlainTextResponse)
def llms_full_txt(request: Request) -> str:
    sections = [llms_txt(request), "\n## Полные тексты\n"]
    for post in agent.storage.list_blog_posts(limit=100):
        path = blog_path_for_kind(post["kind"], post["slug"])
        sections.append(f"\n### {post['title']}\n")
        sections.append(f"URL: {absolute_site_url(path, request)}\n")
        sections.append(clean_article_text(post["content"]).strip())
        sections.append("\n")
    return "\n".join(sections)


@router.get("/rss.xml")
@router.get("/feed.xml")
@router.head("/rss.xml")
@router.head("/feed.xml")
def rss_feed(request: Request) -> Response:
    posts = agent.storage.list_blog_posts(kind="article", limit=100)
    last_build = rss_date(posts[0]["updated_at"] if posts else None)
    channel_link = absolute_site_url("/", request)
    items: list[str] = []
    for post in posts:
        post_link = absolute_site_url(f"/blog/{post['slug']}", request)
        cover_src = media_url(post.get("cover_path"), settings)
        cover_url = absolute_site_url(cover_src, request) if cover_src else ""
        html_content = render_article_html(post["content"])
        if cover_url:
            html_content = (
                f'<p><img src="{escape(cover_url)}" alt="{escape(post["title"])}"></p>\n'
                f"{html_content}"
            )
        media = (
            f'<media:content url="{escape(cover_url)}" medium="image" />'
            if cover_url
            else ""
        )
        items.append(
            f"""
    <item>
      <title>{escape(post["title"])}</title>
      <link>{escape(post_link)}</link>
      <guid isPermaLink="true">{escape(post_link)}</guid>
      <pubDate>{rss_date(post.get("created_at"))}</pubDate>
      <description>{cdata(post.get("excerpt") or excerpt_from_content(post["content"]))}</description>
      <content:encoded>{cdata(html_content)}</content:encoded>
      {media}
    </item>"""
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>AI на миллион — AI, разработка и вайбкодинг</title>
    <link>{escape(channel_link)}</link>
    <description>Статьи про ИИ, разработку, автоматизацию и личные проекты в вайбкодинге.</description>
    <language>ru</language>
    <lastBuildDate>{last_build}</lastBuildDate>
    <ttl>60</ttl>
{''.join(items)}
  </channel>
</rss>
"""
    return Response(xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/", response_class=HTMLResponse)
def public_home(request: Request) -> str:
    articles = agent.storage.list_blog_posts(kind="article", limit=3)
    projects = agent.storage.list_blog_posts(kind="project", limit=3)
    wiki_notes = agent.storage.list_blog_posts(kind="wiki", limit=3)
    body = f"""
      <section class="panel">
        <h2>AI, разработка и вайбкодинг без лишнего шума</h2>
        <p>Здесь я публикую статьи про ИИ, разработку, автоматизацию и личные проекты. Часть проектов можно попробовать прямо на сайте в режиме ограниченного демо-доступа.</p>
        <div class="actions">
          <a class="btn primary" href="/projects">Попробовать проекты</a>
          <a class="btn" href="/blog">Читать статьи</a>
          <a class="btn" href="/wiki">Открыть Wiki</a>
          <a class="btn" href="https://t.me/AI_naMillion" target="_blank" rel="noopener">Telegram-канал</a>
        </div>
      </section>
      <section class="panel">
        <h2>Проекты</h2>
        <div class="blog-grid">{render_blog_cards(projects) or "<p>Проекты скоро появятся.</p>"}</div>
      </section>
      <section class="panel">
        <h2>Последние статьи</h2>
        <div class="blog-grid">{render_blog_cards(articles) or "<p>Статьи скоро появятся.</p>"}</div>
      </section>
      <section class="panel">
        <h2>Wiki-заметки</h2>
        <div class="blog-grid">{render_blog_cards(wiki_notes) or "<p>Wiki скоро наполнится.</p>"}</div>
      </section>
    """
    return public_shell(
        "AI на миллион",
        body,
        "Блог-лаборатория: статьи, AI-инструменты и проекты, которые можно попробовать.",
        request=request,
        path="/",
        description=seo_default_description(),
        schema=organization_schema(request),
    )


