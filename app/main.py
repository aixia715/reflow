import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connect, init_db

templates = Jinja2Templates(directory="app/templates")


def get_conn():
    conn = connect(os.environ.get("REFLOW_DB", "reflow.sqlite"))
    init_db(conn)
    return conn


def create_app() -> FastAPI:
    app = FastAPI(title="Reflow")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.routes import hierarchy, board, log
    app.include_router(hierarchy.router)
    app.include_router(board.router)
    app.include_router(log.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
