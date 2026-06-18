# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.post("/drafts/{draft_id}/growth")
async def draft_growth_brief(draft_id: int) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"]) or {}
    brief = build_telegram_growth_brief(draft, item)
    agent.storage.upsert_draft_variant(draft_id, "telegram_growth_brief", brief)
    body = f"""
      <section class="panel">
        <h2>Telegram growth brief для черновика #{draft_id}</h2>
        <textarea style="min-height: 620px;">{escape(brief)}</textarea>
        <div class="actions">
          <a class="btn primary" href="/drafts/{draft_id}">Вернуться к черновику</a>
          <a class="btn" href="/admin/growth">Пульт роста</a>
        </div>
      </section>
    """
    return HTMLResponse(page_shell("Telegram growth brief", body, "Пост, CTA, офферы и интерактив под рост канала."))


@router.post("/items/{item_id}/draft", response_class=HTMLResponse)
async def create_draft(item_id: int, platform: str = Form(...)) -> str:
    try:
        draft = await agent.draft(item_id, platform)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft["draft_id"], platform, draft["content"])


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
def view_draft(draft_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    return render_draft_page(draft_id, draft["platform"], draft["content"])


@router.post("/drafts/{draft_id}/rewrite", response_class=HTMLResponse)
async def rewrite_existing_draft(
    draft_id: int,
    content: str = Form(...),
    rewrite_instructions: str = Form(""),
) -> str:
    current_draft = agent.storage.get_draft(draft_id)
    if not current_draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_before_rewrite = clean_article_text(content)
    agent.storage.save_draft_revision(
        draft_id,
        clean_before_rewrite,
        "Перед рерайтом",
    )
    style_text = active_style_text()
    try:
        draft = await agent.rewrite(
            draft_id,
            clean_before_rewrite,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft["draft_id"], draft["platform"], draft["content"])


@router.post("/drafts/{draft_id}/history/{revision_id}/restore", response_class=HTMLResponse)
async def restore_draft_revision(draft_id: int, revision_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    revision = agent.storage.get_draft_revision(revision_id, draft_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Версия не найдена")
    agent.storage.save_draft_revision(draft_id, draft["content"], "Перед откатом")
    restored_content = clean_article_text(revision["content"])
    agent.storage.update_draft_content(draft_id, restored_content)
    return render_draft_page(draft_id, draft["platform"], restored_content)


@router.post("/drafts/{draft_id}/variants", response_class=HTMLResponse)
async def generate_draft_variants(
    draft_id: int,
    content: str = Form(...),
    destinations: Annotated[list[str], Form()] = [],
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    selected = [item for item in destinations if item in DESTINATION_PLATFORMS]
    if not selected:
        selected = list(DESTINATION_PLATFORMS)
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content)
    style_text = active_style_text()
    for destination in selected:
        await agent.generate_variant(draft_id, destination, clean_content, style_text=style_text)
    return render_draft_page(draft_id, draft["platform"], clean_content)


@router.post("/drafts/{draft_id}/compare", response_class=HTMLResponse)
async def generate_draft_compare(
    draft_id: int,
    content: str = Form(...),
    rewrite_instructions: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_content = clean_article_text(content)
    agent.storage.save_draft_revision(draft_id, clean_content, "Перед AI Compare")
    style_text = active_style_text()
    try:
        await agent.compare_rewrites(
            draft_id,
            clean_content,
            style_text=style_text,
            rewrite_instructions=rewrite_instructions,
            limit=3,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return render_draft_page(draft_id, draft["platform"], clean_content)


@router.post("/drafts/{draft_id}/compare/{variant_id}/apply", response_class=HTMLResponse)
async def apply_draft_compare_variant(draft_id: int, variant_id: int) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    variant = agent.storage.get_draft_compare_variant(draft_id, variant_id)
    if not variant:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    agent.storage.save_draft_revision(draft_id, draft["content"], "Перед применением AI Compare")
    content = clean_article_text(variant["content"])
    agent.storage.update_draft_content(draft_id, content)
    agent.storage.mark_draft_compare_variant_selected(draft_id, variant_id)
    return render_draft_page(draft_id, draft["platform"], content)


@router.post("/drafts/{draft_id}/research", response_class=HTMLResponse)
async def generate_draft_research_report(
    draft_id: int,
    content: str = Form(""),
    research_question: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    clean_content = clean_article_text(content) or clean_article_text(draft["content"])
    agent.storage.update_draft_content(draft_id, clean_content)
    report = build_research_report(draft, item, clean_content, research_question)
    agent.storage.add_research_report(draft_id, draft["item_id"], f"Research: {item['title']}", report)
    agent.storage.add_task_note(
        "Проверить факты перед публикацией",
        "Открыть источник, сверить даты/цифры и добавить авторский вывод после Research Report.",
        draft_id=draft_id,
        item_id=draft["item_id"],
    )
    return render_draft_page(draft_id, draft["platform"], clean_content)


@router.post("/drafts/{draft_id}/notes", response_class=HTMLResponse)
async def add_draft_task_note(
    draft_id: int,
    note_title: str = Form(""),
    note_content: str = Form(""),
    due_at: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    title = note_title.strip() or "Задача по черновику"
    agent.storage.add_task_note(
        title,
        note_content,
        draft_id=draft_id,
        item_id=draft["item_id"],
        due_at=due_at.strip() or None,
    )
    return render_draft_page(draft_id, draft["platform"], draft["content"])


@router.post("/drafts/{draft_id}/image/generate", response_class=HTMLResponse)
async def generate_draft_image(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    try:
        path, prompt, source = await generate_image_for_topic(
            item["title"],
            item.get("summary", ""),
            image_prompt,
            settings,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Картинка не сгенерировалась",
                message=str(exc),
                detail="Проверь провайдера в настройках или временно выбери fallback-обложку без API.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=str(path),
        prompt=prompt,
        source=source,
    )
    return render_draft_page(draft_id, draft["platform"], content)


@router.post("/drafts/{draft_id}/image/generate-batch", response_class=HTMLResponse)
async def generate_draft_image_batch(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    created = 0
    errors: list[str] = []
    for index in range(4):
        try:
            path, prompt, source = await generate_image_for_topic(
                item["title"],
                item.get("summary", ""),
                f"{image_prompt}\nVariant {index + 1}: unique composition, same article idea.".strip(),
                settings,
                image_config=image_generation_config(),
            )
        except ImageGenerationError as exc:
            errors.append(str(exc))
            continue
        agent.storage.save_media_asset(
            draft_id=draft_id,
            item_id=draft["item_id"],
            path=str(path),
            prompt=prompt,
            source=f"{source}_batch",
            kind="image",
        )
        created += 1
    if created == 0:
        return HTMLResponse(
            publish_result_page(
                title="Варианты обложки не сгенерировались",
                message=errors[0] if errors else "Провайдер не вернул изображения.",
                detail="Проверь MuAPI/OpenRouter/Cloudflare настройки или верни fallback.",
                is_error=True,
            ),
            status_code=200,
        )
    return render_draft_page(draft_id, draft["platform"], content)


@router.post("/drafts/{draft_id}/video/generate", response_class=HTMLResponse)
async def generate_draft_video(
    draft_id: int,
    content: str = Form(...),
    image_prompt: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    item = agent.storage.get_item(draft["item_id"])
    if not item:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    agent.storage.update_draft_content(draft_id, content)
    try:
        video_url, prompt, source = await generate_video_for_topic(
            item["title"],
            item.get("summary", ""),
            image_prompt,
            image_url=None,
            image_config=image_generation_config(),
        )
    except ImageGenerationError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Видео-анонс не сгенерировался",
                message=str(exc),
                detail="Для видео нужен MuAPI key. Sandbox key подойдёт для проверки механики без списания кредитов.",
                is_error=True,
            ),
            status_code=200,
        )
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=video_url,
        prompt=prompt,
        source=source,
        kind="video",
    )
    return render_draft_page(draft_id, draft["platform"], content)


@router.post("/drafts/{draft_id}/image/upload", response_class=HTMLResponse)
async def upload_draft_image(
    draft_id: int,
    content: str = Form(...),
    image_file: UploadFile | None = File(None),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    if not image_file or not image_file.filename:
        return publish_result_page(
            title="Загрузка картинки не удалась",
            message="Файл не выбран.",
            detail="Вернись к черновику и выбери изображение.",
            is_error=True,
        )
    suffix = Path(image_file.filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        return publish_result_page(
            title="Загрузка картинки не удалась",
            message="Поддерживаются PNG, JPG и WEBP.",
            is_error=True,
        )
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_filename(Path(image_file.filename).stem)}-{uuid4().hex[:8]}{suffix}"
    path = settings.media_dir / filename
    path.write_bytes(await image_file.read())
    agent.storage.update_draft_content(draft_id, content)
    agent.storage.save_media_asset(
        draft_id=draft_id,
        item_id=draft["item_id"],
        path=str(path),
        prompt="manual upload",
        source="upload",
    )
    return render_draft_page(draft_id, draft["platform"], content)


@router.get("/drafts/{draft_id}/image/delete")
def delete_draft_image_get(draft_id: int) -> RedirectResponse:
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)


@router.post("/drafts/{draft_id}/image/delete", response_class=HTMLResponse)
async def delete_draft_image(
    draft_id: int,
    content: str = Form(""),
    image_asset_id: int | None = Form(None),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    current_content = content or draft["content"]
    agent.storage.update_draft_content(draft_id, current_content)
    latest_image = agent.storage.get_latest_media_asset(draft_id)
    target_asset_id = image_asset_id or (latest_image["id"] if latest_image else None)
    deleted = (
        agent.storage.delete_media_asset(target_asset_id, draft_id)
        if target_asset_id is not None
        else None
    )
    if deleted:
        try:
            path = Path(deleted["path"]).resolve()
            if path.is_relative_to(settings.media_dir.resolve()) and path.exists():
                path.unlink()
        except OSError:
            pass
    return render_draft_page(draft_id, draft["platform"], current_content)


@router.post("/drafts/{draft_id}/media/{asset_id}/select", response_class=HTMLResponse)
async def select_draft_media(
    draft_id: int,
    asset_id: int,
    content: str = Form(""),
) -> str:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    current_content = content or draft["content"]
    agent.storage.update_draft_content(draft_id, current_content)
    if not agent.storage.mark_media_asset_current(asset_id, draft_id):
        raise HTTPException(status_code=404, detail="Медиа не найдено")
    return render_draft_page(draft_id, draft["platform"], current_content)


@router.post("/drafts/{draft_id}/blog")
async def publish_draft_to_blog(
    draft_id: int,
    content: str = Form(...),
    blog_kind: str = Form("article"),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
) -> RedirectResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    clean_content = clean_article_text(content)
    kind = blog_kind if blog_kind in {"article", "project", "wiki"} else "article"
    agent.storage.update_draft_content(draft_id, clean_content, "published")
    _, path = create_blog_post_from_draft(
        draft_id,
        draft,
        clean_content,
        blog_kind=kind,
        demo_url=demo_url,
        trial_limit=trial_limit,
    )
    return RedirectResponse(path, status_code=303)


@router.post("/drafts/{draft_id}/schedule")
async def schedule_draft_publication(
    draft_id: int,
    platform: str = Form(...),
    content: str = Form(...),
    scheduled_at: str = Form(...),
    destinations: Annotated[list[str] | None, Form()] = None,
    request: Request = None,
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    form = await request.form() if request else {}
    selected = selected_destinations_from_form(form, destinations, platform)
    if not selected:
        return HTMLResponse(
            publish_result_page(
                title="Планирование не удалось",
                message="Выбери хотя бы одну площадку в блоке «Куда отправить».",
                is_error=True,
            ),
            status_code=400,
        )
    try:
        scheduled_dt = date_parser.parse(scheduled_at).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return HTMLResponse(
            publish_result_page(
                title="Планирование не удалось",
                message="Не получилось разобрать дату публикации.",
                detail="Выбери дату и время в поле «Отложить».",
                is_error=True,
            ),
            status_code=400,
        )
    image = agent.storage.get_latest_media_asset(draft_id)
    image_path = image["path"] if image else None
    form_content = {key: str(value) for key, value in form.items() if key.startswith("content_")}
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content, "scheduled")
    queue_ids = []
    for destination in selected:
        queue_ids.append(
            agent.storage.schedule_publication(
                draft_id=draft_id,
                item_id=draft["item_id"],
                platform=destination,
                content=destination_content(form_content, destination, clean_content),
                scheduled_at=scheduled_dt.isoformat(),
                image_path=image_path,
            )
        )
    return HTMLResponse(
        publish_result_page(
            title="Запланировано",
            message=f"Публикации поставлены в очередь: {', '.join(f'#{item}' for item in queue_ids)}.",
            detail=f"Площадки: {', '.join(platform_label(item) for item in selected)}. UTC: {scheduled_dt.isoformat()}",
        )
    )


@router.post("/drafts/{draft_id}/publish")
async def publish_draft(
    draft_id: int, platform: str = Form(...), content: str = Form(...)
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    if platform in {"blog", "blog_project", "wiki"}:
        kind = {"blog": "article", "blog_project": "project", "wiki": "wiki"}[platform]
        clean_content = clean_article_text(content)
        agent.storage.update_draft_content(draft_id, clean_content, "published")
        _, path = create_blog_post_from_draft(draft_id, draft, clean_content, blog_kind=kind)
        agent.storage.save_publication(
            draft_id=draft_id,
            item_id=draft["item_id"],
            platform=platform,
            status="published",
            content=clean_content,
            response={"status": "published", "path": path},
            image_path=(agent.storage.get_latest_media_asset(draft_id) or {}).get("path"),
        )
        return HTMLResponse(
            publish_result_page(
                title="Страница создана",
                message=f"Площадка: {platform_label(platform)}",
                detail=f'<a href="{escape(path)}">{escape(path)}</a>',
            )
        )
    try:
        image = agent.storage.get_latest_media_asset(draft_id)
        image_path = image["path"] if image else None
        result = await publish(
            platform,
            content,
            settings,
            image_path=image_path,
            overrides=publish_overrides(),
        )
    except PublishError as exc:
        return HTMLResponse(
            publish_result_page(
                title="Публикация не удалась",
                message=str(exc),
                detail="Проверь токены, права бота и ID площадки.",
                is_error=True,
            ),
            status_code=400,
        )
    agent.storage.update_draft_content(
        draft_id,
        content,
        "published" if platform in {"telegram", "max", "vk"} else "ready",
    )
    agent.storage.save_publication(
        draft_id=draft_id,
        item_id=draft["item_id"],
        platform=platform,
        status="published" if platform in {"telegram", "max", "vk"} else "ready",
        content=content,
        response=result,
        image_path=image_path,
    )
    message = "Опубликовано" if platform in {"telegram", "max", "vk"} else "Черновик готов"
    detail = result.get("message", "") if isinstance(result, dict) else ""
    return HTMLResponse(
        publish_result_page(title=message, message=f"Площадка: {platform_label(platform)}", detail=detail)
    )


@router.post("/drafts/{draft_id}/publish/multi")
async def publish_draft_multi(
    draft_id: int,
    platform: str = Form(...),
    content: str = Form(...),
    destinations: Annotated[list[str] | None, Form()] = None,
    blog_kind: str = Form("article"),
    demo_url: str = Form(""),
    trial_limit: int = Form(5),
    request: Request = None,
) -> HTMLResponse:
    draft = agent.storage.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    form = await request.form() if request else {}
    form_content = {key: str(value) for key, value in form.items() if key.startswith("content_")}
    selected = selected_destinations_from_form(form, destinations, platform)
    if not selected:
        return HTMLResponse(
            publish_result_page(
                title="Нечего публиковать",
                message="Выбери хотя бы одну площадку в блоке «Куда отправить».",
                is_error=True,
            ),
            status_code=400,
        )

    image = agent.storage.get_latest_media_asset(draft_id)
    image_path = image["path"] if image else None
    clean_content = clean_article_text(content)
    agent.storage.update_draft_content(draft_id, clean_content, "published")
    results: list[str] = []
    errors: list[str] = []

    for destination in selected:
        if destination in {"blog", "blog_project", "wiki"}:
            target_content = destination_content(form_content, destination, clean_content)
            kind = {"blog": "article", "blog_project": "project", "wiki": "wiki"}[destination]
            _, path = create_blog_post_from_draft(
                draft_id,
                draft,
                target_content,
                blog_kind=kind,
                demo_url=demo_url,
                trial_limit=trial_limit,
            )
            result = {"status": "published", "path": path}
            agent.storage.save_publication(
                draft_id=draft_id,
                item_id=draft["item_id"],
                platform=destination,
                status="published",
                content=target_content,
                response=result,
                image_path=image_path,
            )
            results.append(f"{platform_label(destination)}: создано <a href=\"{escape(path)}\">{escape(path)}</a>")
            continue
        try:
            target_content = destination_content(form_content, destination, clean_content)
            result = await publish(
                destination,
                target_content,
                settings,
                image_path=image_path,
                overrides=publish_overrides(),
            )
        except PublishError as exc:
            errors.append(f"{platform_label(destination)}: {escape(str(exc))}")
            continue
        status = "published" if destination in {"telegram", "max", "vk"} else "ready"
        agent.storage.save_publication(
            draft_id=draft_id,
            item_id=draft["item_id"],
            platform=destination,
            status=status,
            content=target_content,
            response=result,
            image_path=image_path,
        )
        results.append(f"{platform_label(destination)}: {'опубликовано' if status == 'published' else 'готово'}")

    status_title = "Готово" if not errors else "Частично готово"
    body = f"""
      <section class="panel">
        <h2>{status_title}</h2>
        <p><span class="pill">Выбрано: {escape(', '.join(platform_label(item) for item in selected))}</span></p>
        <h3>Успешно</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in results) or '<li>Нет успешных действий.</li>'}</ul>
        <h3>Ошибки</h3>
        <ul>{''.join(f'<li>{item}</li>' for item in errors) or '<li>Ошибок нет.</li>'}</ul>
        <div class="actions"><a class="btn" href="/drafts/{draft_id}">Вернуться к черновику</a></div>
      </section>
    """
    return HTMLResponse(page_shell(status_title, body, "Публикация по выбранным площадкам."))


@router.get("/drafts/{draft_id}/publish")
def publish_get_redirect(draft_id: int) -> RedirectResponse:
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)


