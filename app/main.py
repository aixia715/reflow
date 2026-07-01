import os
from urllib.parse import quote
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connect, init_db

from app.badge_config import pcb_badge_class
from app import hashing

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["urlencode"] = lambda v: quote(str(v), safe="")
templates.env.filters["pcb_badge_class"] = pcb_badge_class
# 模板里取节点 / 硬更改的哈希（长用于 title，短用于展示）
templates.env.globals["node_hash"] = hashing.node_hash
templates.env.globals["node_short"] = hashing.node_short
templates.env.globals["hard_hash"] = hashing.hard_change_hash
templates.env.globals["hard_short"] = hashing.hard_change_short


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

    from app.routes import hierarchy, board, log, hard_change, hashes
    app.include_router(hierarchy.router)
    app.include_router(board.router)
    app.include_router(log.router)
    app.include_router(hard_change.router)
    app.include_router(hashes.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/version")
    def version():
        return {"version": os.environ.get("REFLOW_VERSION", "dev")}

    return app


app = create_app()
