import os
from urllib.parse import quote
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connect, init_db

from app.badge_config import pcb_badge_class

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["urlencode"] = lambda v: quote(str(v), safe="")
templates.env.filters["pcb_badge_class"] = pcb_badge_class


def get_conn():
    conn = connect(os.environ.get("REFLOW_DB", "reflow.sqlite"))
    init_db(conn)
    return conn


def create_app() -> FastAPI:
    app = FastAPI(title="Reflow")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    upload_dir = os.environ.get("REFLOW_UPLOAD_DIR", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")

    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import PlainTextResponse

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request, exc):
        if exc.status_code == 404:
            return templates.TemplateResponse(
                request, "404.html", {}, status_code=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    from app.routes import hierarchy, board, log, hard_change
    app.include_router(hierarchy.router)
    app.include_router(board.router)
    app.include_router(log.router)
    app.include_router(hard_change.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
