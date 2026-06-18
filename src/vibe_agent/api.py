"""Vibe Content Agent — FastAPI application entrypoint.

The application was refactored from a single 7k-line module into a package:

    web/core.py            shared layer: imports, globals, constants, helpers, lifespan
    web/routers/*.py       APIRouter modules grouped by domain

This module stays intentionally thin: it builds the FastAPI app, wires the
Basic-Auth middleware and static mount, and includes the domain routers.

The public entrypoint ``vibe_agent.api:app`` is preserved for uvicorn/systemd.
"""

import base64
import secrets

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from vibe_agent.web import core
from vibe_agent.web.core import (
    auth_required,
    is_public_request,
    lifespan,
    settings,
)
from vibe_agent.web.routers import (
    admin,
    blog_admin,
    drafts,
    editorial,
    marketing,
    public,
    public_blog,
    runs,
    settings_admin,
    sources,
    studio,
    styles,
)

app = FastAPI(title="Vibe Content Agent", lifespan=lifespan)
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

# Expose the app on the core module so any legacy reference keeps working.
core.app = app


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    if is_public_request(request) or not settings.admin_password:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return auth_required()
    try:
        decoded = base64.b64decode(auth.removeprefix("Basic ").strip()).decode()
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return auth_required()
    if secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(
        password, settings.admin_password
    ):
        return await call_next(request)
    return auth_required()


# Router include order matters: specific routes (e.g. /blog/admin) must be
# registered before catch-all slug routes (e.g. /blog/{slug}).
app.include_router(public.router)
app.include_router(admin.router)
app.include_router(runs.router)
app.include_router(sources.router)
app.include_router(studio.router)
app.include_router(editorial.router)
app.include_router(settings_admin.router)
app.include_router(styles.router)
app.include_router(marketing.router)
app.include_router(blog_admin.router)
app.include_router(public_blog.router)
app.include_router(drafts.router)
