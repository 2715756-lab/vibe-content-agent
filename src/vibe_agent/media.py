import base64
import asyncio
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageDraw, ImageFont

from vibe_agent.config import Settings
from vibe_agent.llm import normalize_api_key


class ImageGenerationError(RuntimeError):
    pass


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]+", "-", value).strip("-").lower()
    return cleaned[:60] or "image"


def media_url(path: str | None, settings: Settings) -> str | None:
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(settings.media_dir.resolve())
    except ValueError:
        return None
    return f"/media/{relative.as_posix()}"


async def generate_image_for_topic(
    title: str,
    summary: str,
    prompt: str,
    settings: Settings,
    image_config: dict | None = None,
) -> tuple[Path, str, str]:
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    image_config = image_config or {}
    provider = image_config.get("ai_image_provider") or "fallback"
    final_prompt = compact_image_prompt(build_image_prompt(title, summary, prompt))
    filename = f"{safe_filename(title)}-{uuid.uuid4().hex[:8]}.png"
    output_path = settings.media_dir / filename

    providers = [provider]
    if provider != "cloudflare_worker_images" and image_config.get("cloudflare_image_worker_url"):
        providers.append("cloudflare_worker_images")
    if "fallback" not in providers:
        providers.append("fallback")

    errors: list[str] = []
    for candidate in providers:
        try:
            generated = await try_generate_image_provider(
                candidate,
                final_prompt,
                title,
                output_path,
                settings,
                image_config,
            )
        except ImageGenerationError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if generated:
            path, source = generated
            if errors and source != "fallback":
                source = f"{source}_after_fallback"
            return path, final_prompt, source

    raise ImageGenerationError("Не удалось создать картинку: " + " | ".join(errors))


async def try_generate_image_provider(
    provider: str,
    prompt: str,
    title: str,
    output_path: Path,
    settings: Settings,
    image_config: dict,
) -> tuple[Path, str] | None:
    if provider == "openai_images":
        api_key = image_config.get("openai_image_api_key") or settings.openai_api_key
        if not api_key:
            return None
        try:
            client = AsyncOpenAI(api_key=api_key)
            result = await client.images.generate(
                model=image_config.get("openai_image_model") or "gpt-image-1",
                prompt=prompt,
                size="1024x1024",
            )
            image_base64 = result.data[0].b64_json
            if image_base64:
                output_path.write_bytes(base64.b64decode(image_base64))
                return output_path, "openai_images"
        except Exception as exc:
            raise ImageGenerationError(f"OpenAI Images не сгенерировал картинку: {exc}") from exc
        raise ImageGenerationError("OpenAI Images не вернул изображение.")

    if provider == "openrouter_images":
        image_bytes = await generate_openrouter_image(prompt, settings, image_config)
        if image_bytes:
            path = output_path.with_suffix(image_suffix(image_bytes))
            path.write_bytes(image_bytes)
            return path, "openrouter_images"
        raise ImageGenerationError("OpenRouter Images не вернул изображение.")

    if provider == "cloudflare_worker_images":
        image_bytes = await generate_cloudflare_worker_image(prompt, image_config)
        if image_bytes:
            path = output_path.with_suffix(image_suffix(image_bytes))
            path.write_bytes(image_bytes)
            return path, "cloudflare_worker_images"
        raise ImageGenerationError("Cloudflare Worker Images не вернул изображение.")

    if provider == "muapi_images":
        image_bytes = await generate_muapi_image(prompt, image_config)
        if image_bytes:
            path = output_path.with_suffix(image_suffix(image_bytes))
            path.write_bytes(image_bytes)
            return path, "muapi_images"
        raise ImageGenerationError("MuAPI не вернул изображение.")

    if provider == "fallback":
        create_fallback_cover(output_path, title)
        return output_path, "fallback"

    return None


async def generate_video_for_topic(
    title: str,
    summary: str,
    prompt: str,
    image_url: str | None,
    image_config: dict | None = None,
) -> tuple[str, str, str]:
    image_config = image_config or {}
    final_prompt = compact_image_prompt(build_video_prompt(title, summary, prompt), max_chars=1400)
    video_url = await generate_muapi_video(final_prompt, image_url, image_config)
    if not video_url:
        raise ImageGenerationError("MuAPI не вернул ссылку на видео.")
    return video_url, final_prompt, "muapi_video"


async def generate_muapi_image(prompt: str, image_config: dict) -> bytes | None:
    result = await submit_muapi_generation(
        endpoint=image_config.get("muapi_image_model") or "flux-dev-image",
        payload={
            "prompt": prompt,
            "aspect_ratio": image_config.get("muapi_image_aspect_ratio") or "16:9",
            "resolution": image_config.get("muapi_image_resolution") or "1K",
        },
        image_config=image_config,
        max_attempts=90,
    )
    output_url = extract_muapi_output_url(result)
    return await download_url_bytes(output_url) if output_url else None


async def generate_muapi_video(
    prompt: str,
    image_url: str | None,
    image_config: dict,
) -> str | None:
    endpoint = image_config.get("muapi_i2v_model") if image_url else image_config.get("muapi_video_model")
    endpoint = endpoint or ("wan2.2-image-to-video" if image_url else "wan2.2-text-to-video")
    payload = {
        "prompt": prompt,
        "aspect_ratio": image_config.get("muapi_video_aspect_ratio") or "9:16",
        "duration": int(image_config.get("muapi_video_duration") or 5),
    }
    if image_url:
        payload["image_url"] = image_url
    result = await submit_muapi_generation(
        endpoint=endpoint,
        payload=payload,
        image_config=image_config,
        max_attempts=900,
    )
    return extract_muapi_output_url(result)


async def submit_muapi_generation(
    endpoint: str,
    payload: dict,
    image_config: dict,
    max_attempts: int = 90,
) -> dict:
    api_key = normalize_api_key(image_config.get("muapi_api_key"))
    if not api_key:
        raise ImageGenerationError("Для MuAPI нужен API key. Можно создать Sandbox key для бесплатного теста.")
    base_url = (image_config.get("muapi_base_url") or "https://api.muapi.ai").rstrip("/")
    endpoint = endpoint.strip().lstrip("/")
    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    try:
        timeout = httpx.Timeout(180.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/api/v1/{endpoint}", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            request_id = data.get("request_id") or data.get("id")
            if not request_id:
                return data
            poll_url = f"{base_url}/api/v1/predictions/{request_id}/result"
            for _ in range(max_attempts):
                await asyncio.sleep(2)
                poll_response = await client.get(poll_url, headers=headers)
                poll_response.raise_for_status()
                poll_data = poll_response.json()
                status = str(poll_data.get("status") or "").lower()
                if status in {"completed", "succeeded", "success"}:
                    return poll_data
                if status in {"failed", "error", "cancelled"}:
                    raise ImageGenerationError(f"MuAPI generation failed: {poll_data.get('error') or status}")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response is not None else str(exc)
        raise ImageGenerationError(f"MuAPI ответил ошибкой: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise ImageGenerationError("MuAPI не успел ответить.") from exc
    except httpx.RequestError as exc:
        raise ImageGenerationError(f"MuAPI недоступен: {exc}") from exc
    raise ImageGenerationError("MuAPI generation timed out.")


def extract_muapi_output_url(data: dict) -> str | None:
    candidates = [
        data.get("url"),
        data.get("output_url"),
        data.get("video_url"),
        data.get("image_url"),
        (data.get("output") or {}).get("url") if isinstance(data.get("output"), dict) else None,
    ]
    outputs = data.get("outputs")
    if isinstance(outputs, list) and outputs:
        first = outputs[0]
        if isinstance(first, str):
            candidates.insert(0, first)
        elif isinstance(first, dict):
            candidates.insert(0, first.get("url") or first.get("image_url") or first.get("video_url"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    return None


async def download_url_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
    except httpx.RequestError as exc:
        raise ImageGenerationError(f"Не получилось скачать результат MuAPI: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ImageGenerationError(f"MuAPI result URL ответил ошибкой: {exc.response.status_code}") from exc


async def generate_cloudflare_worker_image(prompt: str, image_config: dict) -> bytes | None:
    worker_url = (image_config.get("cloudflare_image_worker_url") or "").strip().rstrip("/")
    api_key = (image_config.get("cloudflare_image_api_key") or "").strip()
    if not worker_url or not api_key:
        return None
    curl_result = await generate_cloudflare_worker_image_with_curl(worker_url, api_key, prompt)
    if curl_result:
        return curl_result
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        timeout = httpx.Timeout(180.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(worker_url, headers=headers, json={"prompt": prompt})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("image/"):
                return response.content
            raise ImageGenerationError(
                f"Cloudflare Worker вернул не картинку: HTTP {response.status_code}, {content_type}"
            )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise ImageGenerationError(f"Cloudflare Worker ответил ошибкой: {detail}") from exc
    except httpx.TimeoutException as exc:
        raise ImageGenerationError("Cloudflare Worker не успел ответить за 180 секунд.") from exc
    except httpx.RequestError as exc:
        raise ImageGenerationError(f"Cloudflare Worker недоступен: {exc}") from exc
    return None


async def generate_cloudflare_worker_image_with_curl(
    worker_url: str,
    api_key: str,
    prompt: str,
) -> bytes | None:
    return await asyncio.to_thread(_generate_cloudflare_worker_image_with_curl, worker_url, api_key, prompt)


def _generate_cloudflare_worker_image_with_curl(worker_url: str, api_key: str, prompt: str) -> bytes | None:
    config_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as config_file:
            config_path = config_file.name
            os.chmod(config_path, 0o600)
            payload = json.dumps({"prompt": prompt}, ensure_ascii=False)
            escaped_url = worker_url.replace("\\", "\\\\").replace('"', '\\"')
            escaped_auth = f"Authorization: Bearer {api_key}".replace("\\", "\\\\").replace('"', '\\"')
            escaped_payload = payload.replace("\\", "\\\\").replace('"', '\\"')
            config_file.write(
                "\n".join(
                    [
                        "silent",
                        "show-error",
                        "fail",
                        "location",
                        "max-time = 75",
                        "request = POST",
                        f'url = "{escaped_url}"',
                        f'header = "{escaped_auth}"',
                        'header = "Content-Type: application/json"',
                        f'data = "{escaped_payload}"',
                    ]
                )
            )
        result = subprocess.run(
            ["curl", "--config", config_path],
            check=False,
            capture_output=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
    if result.returncode == 0 and result.stdout.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")):
        return result.stdout
    return None


async def generate_openrouter_image(
    prompt: str,
    settings: Settings,
    image_config: dict,
) -> bytes | None:
    api_key = normalize_api_key(image_config.get("openrouter_api_key")) or normalize_api_key(
        settings.openai_api_key
    )
    if not api_key:
        return None
    base_url = (image_config.get("openrouter_base_url") or "https://openrouter.ai/api/v1").rstrip("/")
    model = image_config.get("openrouter_image_model") or "black-forest-labs/flux.2-klein-4b"
    modalities = ["image", "text"] if "gemini" in model.lower() else ["image"]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": modalities,
        "stream": False,
        "image_config": {
            "aspect_ratio": image_config.get("openrouter_image_aspect_ratio") or "16:9",
            "image_size": image_config.get("openrouter_image_size") or "1K",
        },
    }
    if model == "recraft/recraft-v3":
        payload["image_config"]["style"] = "Photorealism"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://127.0.0.1:8088",
        "X-Title": "Vibe Content Agent",
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise ImageGenerationError(f"OpenRouter Images ответил ошибкой: {detail}") from exc
    except httpx.RequestError as exc:
        raise ImageGenerationError(f"OpenRouter Images недоступен: {exc}") from exc
    message = (data.get("choices") or [{}])[0].get("message") or {}
    for image in message.get("images") or []:
        url = ((image.get("image_url") or image.get("imageUrl") or {}).get("url")) or image.get("url")
        if isinstance(url, str) and url.startswith("data:image"):
            return base64.b64decode(url.split(",", 1)[1])
    return None


def image_suffix(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return ".webp"
    return ".img"


def build_image_prompt(title: str, summary: str, prompt: str | None) -> str:
    extra = (prompt or "").strip()
    subject = extra or (
        "a focused independent builder working with AI agents, source material, notes, "
        "automation flows and a clean software dashboard in a real modern workspace"
    )
    direction = (
        "Premium editorial cover for a Russian technology and AI article. "
        "Photorealistic documentary-style image, magazine cover quality, tasteful and believable. "
        f"Scene: {subject}. "
        "Composition: strong single focal point, foreground object, layered background, generous negative space "
        "for article layout, 16:9 horizontal crop, no centered symmetrical robot poster. "
        "Lighting: natural window light mixed with soft practical desk light, cinematic but not dark, "
        "subtle contrast, realistic shadows, shallow depth of field. "
        "Color: balanced palette with warm neutral workspace tones, small cyan or green tech accents, "
        "avoid purple-blue gradient look and avoid cheap stock-photo orange-blue cliche. "
        "Details: real desk texture, notebooks, cables, abstract terminal-like shapes only if unreadable, "
        "professional editorial photography, high detail, crisp but not over-sharpened. "
        "Strict negatives: no text, no readable letters, no logos, no watermarks, no UI labels, "
        "no fake charts, no distorted hands, no glossy humanoid robots, no cyberpunk city, "
        "no floating glowing brain, no plastic 3D icons, no generic stock photo, no meme style."
    )
    return (
        f"{direction}\n\n"
        f"Article topic, use as semantic inspiration only, do not render text: {title}\n"
        f"Context: {(summary or '')[:800]}"
    )


def build_video_prompt(title: str, summary: str, prompt: str | None) -> str:
    extra = (prompt or "").strip()
    direction = extra or (
        "Create a 5 second vertical video teaser for a Russian AI and software article. "
        "Editorial, modern, dynamic but clean. Smooth camera motion, premium tech aesthetic, "
        "no text, no logos, no watermarks, no talking heads unless clearly requested."
    )
    return (
        f"{direction}\n\n"
        f"Article topic, use as semantic inspiration only: {title}\n"
        f"Context: {(summary or '')[:900]}"
    )


def compact_image_prompt(prompt: str | None, max_chars: int = 1800) -> str:
    normalized = re.sub(r"\s+", " ", prompt or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    trimmed = normalized[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return trimmed or normalized[:max_chars]


def create_fallback_cover(path: Path, title: str) -> None:
    title = cover_title(title)
    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#f6f1e8")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / height
        r = int(246 * (1 - ratio) + 229 * ratio)
        g = int(241 * (1 - ratio) + 238 * ratio)
        b = int(232 * (1 - ratio) + 227 * ratio)
        draw.line((0, y, width, y), fill=(r, g, b))

    draw.rounded_rectangle((70, 70, width - 70, height - 70), radius=28, fill="#fffaf2")
    draw.rounded_rectangle((70, 70, width - 70, height - 70), radius=28, outline="#1f2937", width=3)
    draw.rectangle((70, 70, width - 70, 178), fill="#111827")
    draw.line((70, 178, width - 70, 178), fill="#d7c8aa", width=3)

    font_label = load_cover_font(30)
    font_brand = load_cover_font(44, bold=True)
    font_title = load_cover_font(66, bold=True)
    font_meta = load_cover_font(26)

    draw.text((116, 104), "AI НА МИЛЛИОН", fill="#f9fafb", font=font_brand)
    draw.text((width - 540, 116), "AI / DEV / VIBECODING", fill="#a7f3d0", font=font_label)

    accent_colors = ["#2dd4bf", "#f59e0b", "#111827", "#b45309"]
    x0, y0 = width - 470, 260
    for index in range(4):
        offset = index * 82
        draw.rounded_rectangle(
            (x0 + offset, y0 + offset // 4, x0 + offset + 150, y0 + offset // 4 + 150),
            radius=22,
            outline=accent_colors[index],
            width=8,
        )
    draw.line((width - 510, 590, width - 180, 335), fill="#111827", width=5)
    draw.line((width - 430, 650, width - 120, 485), fill="#2dd4bf", width=5)

    title_lines = wrap_text(title, 23)[:5]
    y = 280
    for line in title_lines:
        draw.text((116, y), line, fill="#111827", font=font_title)
        y += 84

    draw.text((116, height - 122), "agent.gazon59.ru", fill="#6b7280", font=font_meta)
    image.save(path)


def cover_title(title: str) -> str:
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "AI на практике"


def load_cover_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap_text(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if len(" ".join([*current, word])) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines
