"""C9：安全响应头（nosniff / Referrer-Policy / X-Frame-Options + API 的 CSP）。

前端文档的 CSP 在 `mingri-toutiao-vue/src/security/csp.ts`（构建期注入 HTML，
静态托管也生效）；本文件守的是**后端**这一半：`/api/*` 是纯 JSON，
`default-src 'none'` 才是它的正确策略。

运行：
    cd server-api
    pytest tests/test_security_headers.py -q
"""
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app import config  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.security_headers import API_CSP  # noqa: E402

# 用 __file__ 定位而不是相对路径：从仓库根跑 pytest 时 cwd 不是 server-api
MAIN_SOURCE = (Path(__file__).resolve().parent.parent / "main.py").read_text(
    encoding="utf-8"
)


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


class TestApiCsp:
    def test_api_response_carries_locked_down_csp(self, client):
        r = client.get("/api/news")
        assert r.status_code == 200
        # 无文档上下文 → 不加载任何资源；被嵌套/base 劫持一并禁掉
        assert r.headers["Content-Security-Policy"] == API_CSP
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]

    def test_base_headers_on_api(self, client):
        r = client.get("/api/news")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["X-Frame-Options"] == "DENY"

    def test_error_responses_also_carry_headers(self, client):
        """404 / 401 同样是响应面：注入不挑状态码。"""
        r = client.get("/api/definitely-not-exist")
        assert r.status_code == 404
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Content-Security-Policy"] == API_CSP

    def test_docs_gets_base_headers_but_no_api_csp(self, client):
        """Swagger UI 依赖 CDN 脚本与内联脚本，套 default-src 'none' 会打不开。

        docs 仅在 development 存在（生产 `DOCS_URL=None`），故不为它放宽策略——
        非 /api 路径只拿基础头。
        """
        r = client.get("/docs")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" not in r.headers


class TestSwitch:
    def test_disabled_removes_all_security_headers(self, client, monkeypatch):
        # 读模块属性而非 from-import，正是为了让这里能 monkeypatch
        monkeypatch.setattr(config, "SECURITY_HEADERS_ENABLED", False)
        r = client.get("/api/news")
        assert r.status_code == 200
        assert "Content-Security-Policy" not in r.headers
        assert "X-Frame-Options" not in r.headers
        assert "X-Content-Type-Options" not in r.headers

    def test_reenabled_restores_headers(self, client, monkeypatch):
        """对照组：上一条的"没有头"确实来自开关，而不是接口本身不带。"""
        monkeypatch.setattr(config, "SECURITY_HEADERS_ENABLED", True)
        r = client.get("/api/news")
        assert r.headers["Content-Security-Policy"] == API_CSP


class TestMiddlewareOrder:
    def test_security_headers_registered_outermost(self):
        """后注册 = 最外层：限流直接返回的 429 也要带上这些头。

        Starlette 的 `add_middleware` 往栈头插，所以源码里写在后面的中间件
        反而在外层。把顺序写反，429 / 短路响应就会漏掉安全头。
        """
        assert MAIN_SOURCE.index("security_headers_middleware") > MAIN_SOURCE.index(
            "rate_limit_middleware"
        )
        assert MAIN_SOURCE.index("rate_limit_middleware") > MAIN_SOURCE.index(
            "access_log_middleware"
        )
