# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/blog", response_class=HTMLResponse)
@router.get("/blog/admin", response_class=HTMLResponse)
def blog_admin() -> str:
    posts = agent.storage.list_blog_posts_admin(limit=500)
    rows = "\n".join(render_blog_admin_row(post) for post in posts)
    body = f"""
      <section class="panel">
        <h2>Новая публикация</h2>
        <form class="settings-form" method="post" action="/admin/blog">
          <label>Тип</label>
          <select name="kind">
            <option value="article">Статья</option>
            <option value="project">Проект с демо</option>
            <option value="wiki">Wiki-заметка</option>
          </select>
          <label>Заголовок</label>
          <input name="title" required placeholder="Например: Локальный AI-хаб для вайбкодинга">
          <label>Короткое описание</label>
          <textarea name="excerpt" style="min-height: 110px;" placeholder="1-2 предложения для карточки"></textarea>
          <label>Текст</label>
          <textarea name="content" placeholder="Статья, описание проекта, инструкция, выводы..."></textarea>
          <label>Demo URL</label>
          <input name="demo_url" placeholder="https://... или локальный URL проекта">
          <label>Лимит проб</label>
          <input type="number" name="trial_limit" min="1" max="20" value="5">
          <div class="actions"><button type="submit">Опубликовать в блог</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Опубликованные материалы</h2>
        <p><small>Здесь можно убрать дубли из публичного блога, отредактировать текст, заменить обложку или скрыть материал без удаления.</small></p>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Материал</th>
              <th>Тип</th>
              <th>Статус</th>
              <th>Медиа</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>{rows or '<tr><td colspan="6">Материалов пока нет.</td></tr>'}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Модель доступа</h2>
        <p><span class="pill">3-5 проб</span> человек может попробовать проект без регистрации. Дальше можно давать кнопку на заявку, донат, Telegram или расширенный доступ.</p>
        <p><span class="pill">Статья -> проект</span> статья объясняет идею, проект даёт живой опыт. Это лучше конвертирует, чем просто пост со ссылкой.</p>
      </section>
    """
    return page_shell("Управление блогом", body, "Публикуй статьи и проекты с ограниченным демо-доступом.")


@router.post("/admin/blog")
@router.post("/blog/admin")
async def create_blog_post(
    kind: str = Form("article"),
    title: str = Form(...),
    excerpt: str = Form(""),
    content: str = Form(...),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    clean_kind = kind if kind in {"article", "project", "wiki"} else "article"
    post_id = agent.storage.create_blog_post(
        title=title.strip(),
        slug=make_slug(title),
        kind=clean_kind,
        excerpt=excerpt.strip() or excerpt_from_content(content),
        content=clean_article_text(content),
        demo_url=demo_url.strip() or None,
        trial_limit=max(1, min(trial_limit, 20)),
    )
    slug = make_post_slug_by_id(post_id)
    if clean_kind == "wiki":
        export_wiki_markdown(title.strip(), slug, content)
    schedule_indexnow(blog_path_for_kind(clean_kind, slug))
    return RedirectResponse(blog_path_for_kind(clean_kind, slug), status_code=303)


@router.get("/admin/blog/{post_id}/edit", response_class=HTMLResponse)
def edit_blog_post(post_id: int) -> str:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    cover_src = media_url(post.get("cover_path"), settings)
    cover_block = (
        f"""
        <div class="panel" style="margin-top:10px;">
          <img src="{escape(cover_src or '')}" alt="Обложка" style="width:100%; max-width:520px; aspect-ratio:16/9; object-fit:cover; border-radius:8px; border:1px solid #e0d8ca;">
          <p><small>{escape(post.get('cover_path') or '')}</small></p>
          <form method="post" action="/admin/blog/{post_id}/cover/delete">
            <button type="submit">Удалить обложку</button>
          </form>
        </div>
        """
        if cover_src
        else "<p><small>Обложка не задана.</small></p>"
    )
    body = f"""
      <section class="panel">
        <div class="actions">
          <a class="btn" href="/admin/blog">Назад к блогу</a>
          <a class="btn" href="{escape(blog_path_for_kind(post['kind'], post['slug']))}" target="_blank" rel="noopener">Открыть публично</a>
        </div>
        <h2>Редактирование материала #{post_id}</h2>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/edit">
          <label>Тип</label>
          <select name="kind">
            {select_option('article', 'Статья', post['kind'])}
            {select_option('project', 'Проект с демо', post['kind'])}
            {select_option('wiki', 'Wiki-заметка', post['kind'])}
          </select>
          <label>Статус</label>
          <select name="status">
            {select_option('published', 'Опубликовано', post['status'])}
            {select_option('hidden', 'Скрыто', post['status'])}
            {select_option('archived', 'Архив', post['status'])}
          </select>
          <label>Заголовок</label>
          <input name="title" required value="{escape(post['title'])}">
          <label>Slug</label>
          <input name="slug" value="{escape(post['slug'])}">
          <label>Короткое описание</label>
          <textarea name="excerpt" style="min-height: 120px;">{escape(post.get('excerpt') or '')}</textarea>
          <label>Текст</label>
          <textarea name="content">{escape(post.get('content') or '')}</textarea>
          <label>Demo URL</label>
          <input name="demo_url" value="{escape(post.get('demo_url') or '')}">
          <label>Лимит проб</label>
          <input type="number" name="trial_limit" min="1" max="20" value="{int(post.get('trial_limit') or 5)}">
          <div class="actions">
            <button type="submit" class="primary">Сохранить</button>
          </div>
        </form>
      </section>
      <section class="panel">
        <h2>Медиа статьи</h2>
        {cover_block}
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/image/generate">
          <label>Сгенерировать новую обложку по теме</label>
          <input name="image_prompt" placeholder="Например: редакционный AI-агент анализирует новости, современный tech editorial, без текста и логотипов">
          <div class="actions"><button type="submit">Сгенерировать и поставить обложкой</button></div>
        </form>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/cover" enctype="multipart/form-data">
          <label>Загрузить новую обложку</label>
          <input type="file" name="cover_file" accept="image/*">
          <div class="actions"><button type="submit">Заменить обложку</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>AI-рерайт опубликованной статьи</h2>
        <form class="settings-form" method="post" action="/admin/blog/{post_id}/rewrite">
          <p><small>Берёт текущий текст статьи, активный стиль из <a href="/admin/styles">«Стили»</a> и перезаписывает опубликованный материал. Перед жёсткими правками можно сначала скрыть статью.</small></p>
          <label>Задача на рерайт</label>
          <textarea name="rewrite_instructions" style="min-height: 130px;" placeholder="Например: убери канцелярит, сделай живее, добавь авторский вывод, сохрани факты, без markdown и служебных блоков"></textarea>
          <div class="actions"><button type="submit">Переписать статью AI</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Опасная зона</h2>
        <form method="post" action="/admin/blog/{post_id}/delete" onsubmit="return confirm('Удалить материал, реакции, комментарии и статистику без восстановления?');">
          <button type="submit">Удалить материал полностью</button>
        </form>
      </section>
    """
    return page_shell(f"Редактирование: {post['title']}", body, "Правка опубликованной статьи, проекта или wiki-заметки.")


@router.post("/admin/blog/{post_id}/edit")
async def update_blog_post(
    post_id: int,
    kind: str = Form("article"),
    status: str = Form("published"),
    title: str = Form(...),
    slug: str = Form(""),
    excerpt: str = Form(""),
    content: str = Form(...),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    clean_kind = kind if kind in {"article", "project", "wiki"} else "article"
    clean_status = status if status in {"published", "hidden", "archived"} else "published"
    clean_content = clean_article_text(content)
    clean_slug = re.sub(r"[^a-zA-Z0-9а-яА-Я]+", "-", (slug or title).lower()).strip("-")[:70]
    clean_slug = clean_slug or make_slug_for_post(title, post_id)
    if agent.storage.blog_slug_exists_for_other_post(clean_slug, post_id):
        clean_slug = make_slug_for_post(title, post_id)
    agent.storage.update_blog_post(
        post_id=post_id,
        title=title.strip(),
        slug=clean_slug,
        kind=clean_kind,
        excerpt=excerpt.strip() or excerpt_from_content(clean_content),
        content=clean_content,
        cover_path=post.get("cover_path"),
        demo_url=demo_url.strip() or None,
        trial_limit=max(1, min(trial_limit, 20)),
        status=clean_status,
    )
    if clean_kind == "wiki":
        export_wiki_markdown(title.strip(), clean_slug, clean_content, post.get("cover_path"), post.get("source_draft_id"))
    if clean_status == "published":
        schedule_indexnow(blog_path_for_kind(clean_kind, clean_slug))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@router.post("/admin/blog/{post_id}/status")
async def update_blog_post_status(post_id: int, status: str = Form("published")) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    clean_status = status if status in {"published", "hidden", "archived"} else "published"
    agent.storage.update_blog_post(
        post_id=post_id,
        title=post["title"],
        slug=post["slug"],
        kind=post["kind"],
        excerpt=post.get("excerpt") or "",
        content=post.get("content") or "",
        cover_path=post.get("cover_path"),
        demo_url=post.get("demo_url"),
        trial_limit=int(post.get("trial_limit") or 5),
        status=clean_status,
    )
    return RedirectResponse("/admin/blog", status_code=303)


@router.post("/admin/blog/{post_id}/cover")
async def update_blog_post_cover(post_id: int, cover_file: UploadFile | None = File(None)) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    if cover_file and cover_file.filename:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(cover_file.filename).suffix.lower() or ".png"
        filename = safe_filename(f"blog-{post_id}-{uuid4().hex}{suffix}")
        path = settings.media_dir / filename
        path.write_bytes(await cover_file.read())
        agent.storage.update_blog_post_cover(post_id, str(path))
        if post.get("status") == "published":
            schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@router.post("/admin/blog/{post_id}/cover/delete")
async def delete_blog_post_cover(post_id: int) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    agent.storage.update_blog_post_cover(post_id, None)
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@router.post("/admin/blog/{post_id}/image/generate")
async def generate_blog_post_cover(
    post_id: int,
    image_prompt: str = Form(""),
) -> Response:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    try:
        path, prompt, source = await generate_image_for_topic(
            post["title"],
            post.get("excerpt") or post.get("content") or "",
            image_prompt,
            settings,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Обложка не сгенерировалась",
                message=str(exc),
                detail="Проверь провайдера картинок в настройках или выбери fallback-режим.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.update_blog_post_cover(post_id, str(path))
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@router.post("/admin/blog/{post_id}/rewrite")
async def rewrite_blog_post(
    post_id: int,
    rewrite_instructions: str = Form(""),
) -> RedirectResponse:
    post = agent.storage.get_blog_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Материал не найден")
    style_text = active_style_text()
    platform = "blog_project" if post["kind"] == "project" else "wiki" if post["kind"] == "wiki" else "blog"
    rewritten = await rewrite_draft(
        post.get("content") or "",
        platform,
        settings,
        style_text=style_text,
        rewrite_instructions=rewrite_instructions
        or "Сделай материал живее и читабельнее, сохрани факты, убери markdown, служебные слова и повторы.",
        ai_config=agent.ai_config(),
    )
    clean_content = clean_article_text(rewritten)
    agent.storage.update_blog_post(
        post_id=post_id,
        title=post["title"],
        slug=post["slug"],
        kind=post["kind"],
        excerpt=excerpt_from_content(clean_content),
        content=clean_content,
        cover_path=post.get("cover_path"),
        demo_url=post.get("demo_url"),
        trial_limit=int(post.get("trial_limit") or 5),
        status=post.get("status") or "published",
    )
    if post.get("kind") == "wiki":
        export_wiki_markdown(post["title"], post["slug"], clean_content, post.get("cover_path"), post.get("source_draft_id"))
    if post.get("status") == "published":
        schedule_indexnow(blog_path_for_kind(post["kind"], post["slug"]))
    return RedirectResponse(f"/admin/blog/{post_id}/edit", status_code=303)


@router.post("/admin/blog/{post_id}/delete")
async def delete_blog_post(post_id: int) -> RedirectResponse:
    agent.storage.delete_blog_post(post_id)
    return RedirectResponse("/admin/blog", status_code=303)


