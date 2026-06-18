# AUTO-SPLIT router module.
from vibe_agent.web.core import *  # noqa: F401,F403
from fastapi import APIRouter

router = APIRouter()

@router.get("/admin/styles", response_class=HTMLResponse)
@router.get("/styles", response_class=HTMLResponse)
def styles_page() -> str:
    active_style = agent.storage.get_setting("active_style", "base") or "base"
    styles = list_styles(settings)
    rows = "\n".join(
        f"""
        <tr>
          <td><span class="pill">{'активный' if style['id'] == active_style else 'профиль'}</span></td>
          <td><strong>{escape(style['title'])}</strong><br><small>{escape(style['id'])}</small></td>
          <td>{escape(style['preview'])}</td>
          <td>
            <form method="post" action="/admin/styles/active">
              <input type="hidden" name="style_id" value="{escape(style['id'])}">
              <button type="submit">Сделать активным</button>
            </form>
          </td>
        </tr>
        """
        for style in styles
    )
    options = "\n".join(
        f'<option value="{escape(style["id"])}" {"selected" if style["id"] == active_style else ""}>{escape(style["title"])}</option>'
        for style in styles
    )
    recent_drafts = agent.storage.list_drafts()[:6]
    recent_draft_links = "\n".join(
        f'<a class="btn" href="/drafts/{draft["id"]}">Черновик #{draft["id"]} · {escape(platform_label(draft.get("platform") or ""))}</a>'
        for draft in recent_drafts
    )
    body = f"""
      <section class="panel">
        <h2>Быстро вернуться</h2>
        <div class="actions">{recent_draft_links or '<a class="btn" href="/admin/control">К центру управления</a>'}</div>
        <p><small>После смены стиля открытый черновик не теряется. Вернись к нему здесь и нажми рерайт заново.</small></p>
      </section>
      <section class="panel">
        <h2>Новый профиль стиля</h2>
        <form method="post" action="/admin/styles">
          <label>Название</label>
          <input name="title" placeholder="Например: Telegram личный">
          <label>Правила, примеры, запреты, любимые обороты</label>
          <textarea name="content" style="min-height: 220px;" placeholder="Вставь сюда правила рерайта, примеры своих постов, тональность, структуру, стоп-слова..."></textarea>
          <div class="actions"><button type="submit">Сохранить стиль</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Прикрепить файл к стилю</h2>
        <form method="post" action="/admin/styles/upload" enctype="multipart/form-data">
          <label>К какому стилю добавить</label>
          <select name="style_id">{options}</select>
          <label>Файл .txt или .md</label>
          <input type="file" name="style_file" accept=".txt,.md,text/plain,text/markdown">
          <div class="actions"><button type="submit">Добавить файл к стилю</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Библиотека стилей</h2>
        <table>
          <thead><tr><th>Статус</th><th>Стиль</th><th>Фрагмент</th><th>Действие</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Идеи профилей</h2>
        <p><span class="pill">Telegram</span> быстрый личный пост, больше эмоции и опыта.</p>
        <p><span class="pill">VC</span> аналитика, структура, польза, риски, выводы.</p>
        <p><span class="pill">Дзен</span> шире и понятнее, меньше терминов, больше объяснений.</p>
      </section>
    """
    return page_shell("Стили", body, "Храни правила рерайта, примеры текстов и выбирай активный голос автора.")


@router.post("/admin/styles")
@router.post("/styles")
async def create_style(title: str = Form(...), content: str = Form(...)) -> RedirectResponse:
    style_id = save_style(settings, title, content)
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@router.post("/admin/styles/active")
@router.post("/styles/active")
async def set_active_style(style_id: str = Form(...)) -> RedirectResponse:
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@router.post("/admin/styles/upload")
@router.post("/styles/upload")
async def upload_style_file(
    style_id: str = Form(...),
    style_file: UploadFile | None = File(None),
) -> RedirectResponse:
    if not style_file or not style_file.filename:
        return RedirectResponse("/admin/styles", status_code=303)
    suffix = Path(style_file.filename).suffix.lower()
    if suffix not in {".txt", ".md"}:
        return RedirectResponse("/admin/styles", status_code=303)
    content = (await style_file.read()).decode("utf-8", errors="ignore")
    append_to_style(settings, style_id, style_file.filename, content[:20000])
    agent.storage.set_setting("active_style", style_id)
    return RedirectResponse("/admin/styles", status_code=303)


@router.get("/admin/style-memory", response_class=HTMLResponse)
def style_memory_page() -> str:
    rows = "\n".join(render_style_memory_row(item) for item in agent.storage.list_style_memory())
    body = f"""
      <section class="panel">
        <h2>Память стиля</h2>
        <p><small>Короткие правила, запреты, удачные фразы и примеры. Агент автоматически добавляет их к активному стилю при рерайте, AI Compare и версиях под площадки.</small></p>
        <form class="settings-form" method="post" action="/admin/style-memory">
          <label>Тип</label>
          <select name="kind">
            <option value="rule">Правило</option>
            <option value="ban">Запрет</option>
            <option value="phrase">Удачная фраза</option>
            <option value="example">Пример</option>
          </select>
          <label>Текст</label>
          <textarea name="content" style="min-height: 120px;" placeholder="Например: не начинать с 'в мире ИИ снова...', писать без markdown, добавлять личный вывод"></textarea>
          <label>Вес</label>
          <input type="number" name="weight" min="1" max="10" value="5">
          <div class="actions"><button type="submit">Добавить</button><a class="btn" href="/admin/styles">Профили стиля</a></div>
        </form>
      </section>
      <section class="panel">
        <h2>Текущая память</h2>
        <table>
          <thead><tr><th>Вес</th><th>Тип</th><th>Правило</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Память стиля пока пустая.</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Память стиля", body, "Маленькие правила, которые делают рерайт устойчивее.")


@router.post("/admin/style-memory")
async def add_style_memory(
    kind: str = Form("rule"),
    content: str = Form(""),
    weight: int = Form(5),
) -> RedirectResponse:
    agent.storage.add_style_memory(kind, content, weight)
    return RedirectResponse("/admin/style-memory", status_code=303)


@router.post("/admin/style-memory/{memory_id}/delete")
async def delete_style_memory(memory_id: int) -> RedirectResponse:
    agent.storage.delete_style_memory(memory_id)
    return RedirectResponse("/admin/style-memory", status_code=303)


@router.get("/admin/task-notes", response_class=HTMLResponse)
def task_notes_page(status: str = "") -> str:
    clean_status = status if status in {"open", "waiting", "done"} else None
    notes = agent.storage.list_task_notes(status=clean_status, limit=160)
    rows = "\n".join(render_task_note_row(note) for note in notes)
    body = f"""
      <section class="panel">
        <h2>Task Notes</h2>
        <p><small>Короткие follow-up задачи агента и редактора. Это не таск-трекер, а список маленьких действий, чтобы материалы не терялись.</small></p>
        <div class="actions">
          <a class="btn" href="/admin/task-notes">Все</a>
          <a class="btn" href="/admin/task-notes?status=open">Открытые</a>
          <a class="btn" href="/admin/task-notes?status=waiting">Ожидают</a>
          <a class="btn" href="/admin/task-notes?status=done">Готово</a>
        </div>
        <form class="toolbar" method="post" action="/admin/task-notes">
          <input name="note_title" placeholder="Что сделать">
          <input name="note_content" placeholder="Детали">
          <input type="datetime-local" name="due_at">
          <button type="submit">Добавить</button>
        </form>
      </section>
      <section class="panel">
        <table>
          <thead><tr><th>Статус</th><th>Задача</th><th>Срок</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">Задач пока нет.</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Task Notes", body, "Короткие follow-up действия без тяжёлого таск-трекера.")


@router.post("/admin/task-notes")
async def add_global_task_note(
    note_title: str = Form(""),
    note_content: str = Form(""),
    due_at: str = Form(""),
) -> RedirectResponse:
    title = note_title.strip() or "Задача"
    agent.storage.add_task_note(title, note_content, due_at=due_at.strip() or None)
    return RedirectResponse("/admin/task-notes", status_code=303)


@router.post("/admin/task-notes/{note_id}/status")
async def update_task_note_status(
    note_id: int,
    status: str = Form("open"),
    return_to: str = Form("/admin/task-notes"),
) -> RedirectResponse:
    agent.storage.update_task_note_status(note_id, status)
    return RedirectResponse(safe_return_path(return_to, "/admin/task-notes"), status_code=303)


@router.post("/admin/task-notes/{note_id}/delete")
async def delete_task_note(
    note_id: int,
    return_to: str = Form("/admin/task-notes"),
) -> RedirectResponse:
    agent.storage.delete_task_note(note_id)
    return RedirectResponse(safe_return_path(return_to, "/admin/task-notes"), status_code=303)


@router.get("/admin/model-cookbook", response_class=HTMLResponse)
def model_cookbook_page() -> str:
    entries = agent.storage.list_model_cookbook_entries()
    rows = "\n".join(render_model_cookbook_row(entry) for entry in entries)
    body = f"""
      <section class="panel">
        <h2>Model Cookbook</h2>
        <p><small>Карта моделей по ролям. Это не запускатор всего подряд, а шпаргалка: какая модель для чего, где endpoint, на каком железе держать.</small></p>
        <div class="actions">
          <form method="post" action="/admin/model-cookbook/seed"><button type="submit">Добавить пресеты</button></form>
          <a class="btn" href="/admin/settings">AI-настройки</a>
          <a class="btn" href="/admin/server">Сервер</a>
        </div>
        <form class="settings-form" method="post" action="/admin/model-cookbook">
          <label>Название</label>
          <input name="name" placeholder="Qwen 2.5 7B local">
          <label>Провайдер</label>
          <input name="provider" placeholder="Ollama, OpenRouter, Hugging Face, Gemini">
          <label>Роль</label>
          <input name="role" placeholder="быстрый рерайт, аналитика, заголовки, локальный fallback">
          <label>Endpoint</label>
          <input name="endpoint" placeholder="http://localhost:11434/v1 или https://openrouter.ai/api/v1">
          <label>Model ID</label>
          <input name="model_id" placeholder="qwen2.5:7b-instruct, google/gemini-2.5-flash">
          <label>Железо</label>
          <input name="hardware" placeholder="MacBook, Proxmox node, VPS, API">
          <label>Заметки</label>
          <textarea name="notes" style="min-height: 100px;" placeholder="Когда использовать, ограничения, качество, стоимость"></textarea>
          <label>Статус</label>
          <select name="status">
            <option value="candidate">Кандидат</option>
            <option value="active">Активная</option>
            <option value="fallback">Резерв</option>
            <option value="paused">Пауза</option>
          </select>
          <div class="actions"><button type="submit">Добавить</button></div>
        </form>
      </section>
      <section class="panel">
        <h2>Модели по ролям</h2>
        <table>
          <thead><tr><th>Статус</th><th>Модель</th><th>Роль</th><th>Endpoint</th><th>Действие</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="5">Cookbook пуст. Нажми «Добавить пресеты».</td></tr>'}</tbody>
        </table>
      </section>
    """
    return page_shell("Model Cookbook", body, "Карта моделей без лишнего комбайна.")


@router.post("/admin/model-cookbook")
async def add_model_cookbook_entry(
    name: str = Form(""),
    provider: str = Form(""),
    role: str = Form(""),
    endpoint: str = Form(""),
    model_id: str = Form(""),
    hardware: str = Form(""),
    notes: str = Form(""),
    status: str = Form("candidate"),
) -> RedirectResponse:
    if name.strip():
        agent.storage.add_model_cookbook_entry(
            name,
            provider=provider,
            role=role,
            endpoint=endpoint,
            model_id=model_id,
            hardware=hardware,
            notes=notes,
            status=status,
        )
    return RedirectResponse("/admin/model-cookbook", status_code=303)


@router.post("/admin/model-cookbook/seed")
async def seed_model_cookbook() -> RedirectResponse:
    existing = {
        (entry["provider"], entry["model_id"], entry["role"])
        for entry in agent.storage.list_model_cookbook_entries(limit=500)
    }
    presets = [
        {
            "name": "OpenRouter Auto",
            "provider": "OpenRouter",
            "role": "ежедневные черновики и обычный рерайт",
            "endpoint": "https://openrouter.ai/api/v1",
            "model_id": "openrouter/auto",
            "hardware": "API",
            "notes": "Главный универсальный режим, когда нужен баланс качества и скорости.",
            "status": "active",
        },
        {
            "name": "Gemini Flash",
            "provider": "Gemini",
            "role": "быстрый рерайт, короткие посты, идеи",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta",
            "model_id": "gemini-flash-latest",
            "hardware": "API",
            "notes": "Быстро и дёшево, полезно для Telegram и черновых вариантов.",
            "status": "candidate",
        },
        {
            "name": "DeepSeek V3 HF",
            "provider": "Hugging Face Router",
            "role": "бюджетный резерв для рерайта",
            "endpoint": "https://router.huggingface.co/v1",
            "model_id": "deepseek-ai/DeepSeek-V3-0324:cheapest",
            "hardware": "API",
            "notes": "Использовать как fallback, качество проверять через AI Compare.",
            "status": "fallback",
        },
        {
            "name": "Qwen 2.5 7B local",
            "provider": "Ollama",
            "role": "локальный быстрый резерв без внешних токенов",
            "endpoint": "http://localhost:11434/v1",
            "model_id": "qwen2.5:7b-instruct",
            "hardware": "MacBook / Proxmox",
            "notes": "Подходит для простых задач, заголовков, классификации и черновых подсказок.",
            "status": "candidate",
        },
        {
            "name": "Qwen 2.5 14B local",
            "provider": "Ollama",
            "role": "локальная аналитика, если хватает RAM/VRAM",
            "endpoint": "http://localhost:11434/v1",
            "model_id": "qwen2.5:14b-instruct",
            "hardware": "Proxmox node / мощный Mac",
            "notes": "Пробовать для Research Report и структурирования, если скорость приемлемая.",
            "status": "candidate",
        },
    ]
    for preset in presets:
        key = (preset["provider"], preset["model_id"], preset["role"])
        if key not in existing:
            agent.storage.add_model_cookbook_entry(**preset)
    return RedirectResponse("/admin/model-cookbook", status_code=303)


@router.post("/admin/model-cookbook/{entry_id}/delete")
async def delete_model_cookbook_entry(entry_id: int) -> RedirectResponse:
    agent.storage.delete_model_cookbook_entry(entry_id)
    return RedirectResponse("/admin/model-cookbook", status_code=303)


