import re
from pathlib import Path

from vibe_agent.config import Settings


DEFAULT_STYLE_ID = "base"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]+", "-", value.strip().lower()).strip("-")
    return cleaned[:70] or DEFAULT_STYLE_ID


def ensure_default_style(settings: Settings) -> None:
    settings.style_profiles_dir.mkdir(parents=True, exist_ok=True)
    base_path = style_path(settings, DEFAULT_STYLE_ID)
    if not base_path.exists():
        text = settings.style_profile_path.read_text(encoding="utf-8")
        base_path.write_text(text, encoding="utf-8")


def style_path(settings: Settings, style_id: str) -> Path:
    return settings.style_profiles_dir / f"{slugify(style_id)}.md"


def list_styles(settings: Settings) -> list[dict[str, str]]:
    ensure_default_style(settings)
    styles = []
    for path in sorted(settings.style_profiles_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="replace")
        title = extract_title(content) or path.stem
        styles.append({"id": path.stem, "title": title, "preview": content[:240]})
    return styles


def read_style(settings: Settings, style_id: str | None) -> str:
    ensure_default_style(settings)
    path = style_path(settings, style_id or DEFAULT_STYLE_ID)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return style_path(settings, DEFAULT_STYLE_ID).read_text(encoding="utf-8")


def save_style(settings: Settings, title: str, content: str) -> str:
    ensure_default_style(settings)
    style_id = slugify(title)
    body = content.strip()
    if not body.startswith("#"):
        body = f"# {title.strip() or style_id}\n\n{body}"
    style_path(settings, style_id).write_text(body + "\n", encoding="utf-8")
    return style_id


def append_to_style(settings: Settings, style_id: str, title: str, content: str) -> None:
    ensure_default_style(settings)
    path = style_path(settings, style_id)
    if not path.exists():
        path = style_path(settings, DEFAULT_STYLE_ID)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n## {title.strip() or 'Дополнение'}\n\n{content.strip()}\n")


def extract_title(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
