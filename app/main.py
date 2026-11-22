import os
from urllib.parse import quote
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connect, init_db
from app.paths import resource_dir

from app.badge_config import pcb_badge_class
from app import hashing

_RES = resource_dir()
templates = Jinja2Templates(directory=str(_RES / "templates"))
templates.env.filters["urlencode"] = lambda v: quote(str(v), safe="")
templates.env.filters["pcb_badge_class"] = pcb_badge_class
# 模板里取节点 / 硬更改的哈希（长用于 title，短用于展示）
templates.env.globals["node_hash"] = hashing.node_hash
templates.env.globals["node_short"] = hashing.node_short
templates.env.globals["hard_hash"] = hashing.hard_change_hash
templates.env.globals["hard_short"] = hashing.hard_change_short

# 路由一律走 templates.TemplateResponse。这里包一层，把当前用户和 CSRF 令牌注入上下文。
# 不往 context 里放 request（Starlette 1.x 会和自带的 request 冲突）。
_raw_template_response = templates.TemplateResponse


def _template_response(request, name, context=None, **kwargs):
    context = dict(context or {})
    state = getattr(request, "state", None)
    context.setdefault("user", getattr(state, "user", None))
    context.setdefault("csrf_token", getattr(state, "csrf", ""))
    return _raw_template_response(request, name, context, **kwargs)


templates.TemplateResponse = _template_response


def get_conn():
    conn = connect(os.environ.get("REFLOW_DB", "reflow.sqlite"))
    init_db(conn)
    # 引导只在用户表为空且环境变量齐全时插入一次，每个请求调用是安全的
    from app.cli import bootstrap_admin_from_env
    bootstrap_admin_from_env(conn)
    return conn


def create_app() -> FastAPI:
    from app.authz import SessionAuthMiddleware, require_registered

    # 关掉默认文档：这不是对外 API，/docs 等于把路由表公开。
    app = FastAPI(
        title="Reflow",
        docs_url=None, redoc_url=None, openapi_url=None,
        dependencies=[Depends(require_registered)],
    )
    app.add_middleware(SessionAuthMiddleware)
    app.mount("/static", StaticFiles(directory=str(_RES / "static")), name="static")
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

    from app.routes import hierarchy, board, log, hard_change, hashes, auth
    app.include_router(hierarchy.router)
    app.include_router(board.router)
    app.include_router(log.router)
    app.include_router(hard_change.router)
    app.include_router(hashes.router)
    app.include_router(auth.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/version")
    def version():
        return {"version": os.environ.get("REFLOW_VERSION", "dev")}

    return app


app = create_app()
