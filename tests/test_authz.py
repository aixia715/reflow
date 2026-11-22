"""守卫表必须和真实路由一致：漏登记或表里留着已经删掉的路由都让测试失败。"""

from app.authz import GUARD, WHITELIST
from app.main import create_app


def _iter_route_keys(app):
    def walk(routes):
        for route in routes:
            name = type(route).__name__
            if name == "_IncludedRouter":
                yield from walk(route.original_router.routes)
                continue
            if name == "Mount":
                if route.path == "/static":
                    continue
                yield ("GET", route.path)
                continue
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", None)
            if not path:
                continue
            for method in methods:
                if method in {"HEAD", "OPTIONS"}:
                    continue
                yield (method, path)

    yield from walk(app.routes)


def test_every_route_is_registered_exactly_once():
    app = create_app()
    seen = set(_iter_route_keys(app))
    assert not (seen & set(GUARD.keys()) & WHITELIST)
    missing = sorted(seen - WHITELIST - set(GUARD))
    extra = sorted(set(GUARD) - seen)
    assert not missing, f"路由未登记权限: {missing}"
    assert not extra, f"守卫表有已经不存在的路由: {extra}"
    assert seen - WHITELIST == set(GUARD)
