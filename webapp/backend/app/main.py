from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import (
    auth as auth_api,
    github_picker as github_picker_api,
    github_stats as github_stats_api,
    health,
    ingest as ingest_api,
    og as og_api,
    seo as seo_api,
    traces as traces_api,
    uploads as uploads_api,
)
from app.api.spa_seo import render_spa_html
from app.deps import init_state
from app.settings import get_settings


_PLACEHOLDER_HTML = """<!doctype html>
<html><head><title>vibeshub</title></head>
<body>
<h1>vibeshub</h1>
<p>Frontend build not present. Run <code>npm run build</code> in
<code>webapp/frontend</code> to populate <code>dist/</code>, then redeploy.</p>
</body></html>"""


_frontend_dist_override: Path | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_state(app)
    try:
        yield
    finally:
        await app.state.public_github.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="vibeshub", version="0.6.1", lifespan=lifespan)

    # SessionMiddleware drives Authlib's `state` storage during the OAuth
    # dance. Its cookie ("oauth_state") is distinct from our app session
    # cookie ("vibeshub_session"); short-lived (10 min).
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret or "dev-placeholder-secret-not-for-prod",
        session_cookie="oauth_state",
        same_site="lax",
        https_only=settings.cookie_secure,
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(ingest_api.router)
    app.include_router(traces_api.router)
    app.include_router(uploads_api.router)
    app.include_router(auth_api.router)
    app.include_router(github_stats_api.router)
    app.include_router(github_picker_api.router)
    # Dynamic social-card images (/api/og/<id>.png) — before the SPA catch-all
    # so the route claims the path instead of index.html.
    app.include_router(og_api.router)
    # SEO routes (/robots.txt, /sitemap.xml) must be included before the SPA
    # catch-all below — otherwise the catch-all swallows them and returns
    # index.html instead of the XML/text response.
    app.include_router(seo_api.router)

    frontend_dist = _frontend_dist_override or (
        Path(__file__).resolve().parent.parent / "frontend_dist"
    )
    if (frontend_dist / "index.html").is_file():
        if (frontend_dist / "assets").is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=frontend_dist / "assets"),
                name="spa-assets",
            )
        index_html = (frontend_dist / "index.html").read_text()
        dist_root = frontend_dist.resolve()

        @app.get("/{full_path:path}")
        async def _spa(full_path: str, request: Request) -> Response:
            # Vite's `public/` dir lands files at the root of dist/ — e.g.
            # /favicon.svg, /og-default.png. Serve those as real files
            # before the SPA catch-all swallows the request and returns
            # index.html. index.html itself is rendered through the SEO
            # path below, never as a FileResponse.
            if full_path:
                candidate = frontend_dist / full_path
                try:
                    resolved = candidate.resolve()
                    if (
                        candidate.is_file()
                        and resolved.is_relative_to(dist_root)
                        and resolved.name != "index.html"
                    ):
                        return FileResponse(resolved)
                except (OSError, ValueError):
                    pass

            # For known trace, user, repo, or PR-list URL shapes, swap the
            # default <head> meta block for route-specific tags so social
            # scrapers (which don't run JS) get real link previews. Every
            # other path falls through to the unmodified template — see
            # app/api/spa_seo.py.
            session_maker = request.app.state.session_maker
            base_url = request.app.state.settings.public_base_url
            async with session_maker() as db:
                html = await render_spa_html(
                    index_html, full_path, db, base_url
                )
            return HTMLResponse(html)
    else:
        @app.get("/", response_class=HTMLResponse)
        async def _root() -> str:
            return _PLACEHOLDER_HTML

    return app


app = create_app()
