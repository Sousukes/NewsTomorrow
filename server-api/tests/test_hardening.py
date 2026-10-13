"""批次 H 加固单元测试：受信代理 IP 解析、游标编解码。

运行：
    cd server-api
    pytest -q
"""
import ipaddress
import os

import pytest

# 与 test_api.py 保持一致的隔离环境（必须在导入 main 前设置）
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests-32bytes")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("TRUSTED_PROXIES", "")
os.environ.setdefault("SEED_SAMPLE_DATA", "false")
os.environ.setdefault("FEED_CRON_ENABLED", "false")

from starlette.requests import Request  # noqa: E402

import main  # noqa: E402
from app.routers.news import _decode_cursor, _encode_cursor  # noqa: E402


def make_request(client_host: str = "1.2.3.4", xff: str | None = None) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("ascii")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


class TestClientIp:
    """H3：仅当直连来源在受信代理网段内时才采信 X-Forwarded-For。"""

    def test_ignores_xff_without_trusted_proxy(self):
        req = make_request("1.2.3.4", "9.9.9.9")
        assert main.client_ip(req) == "1.2.3.4"

    def test_uses_xff_when_direct_is_trusted(self, monkeypatch):
        monkeypatch.setattr(main, "_TRUSTED_NETS", [ipaddress.ip_network("10.0.0.0/8")])
        req = make_request("10.0.0.5", "9.9.9.9, 10.0.0.1")
        assert main.client_ip(req) == "9.9.9.9"

    def test_ignores_xff_when_direct_untrusted(self, monkeypatch):
        monkeypatch.setattr(main, "_TRUSTED_NETS", [ipaddress.ip_network("10.0.0.0/8")])
        req = make_request("1.2.3.4", "9.9.9.9")
        assert main.client_ip(req) == "1.2.3.4"

    def test_no_client_falls_back(self):
        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "client": None}
        assert main.client_ip(Request(scope)) == "unknown"


class TestCursor:
    """H5：游标编解码。"""

    class _N:
        def __init__(self, is_top: int, published_at, nid: str):
            self.is_top = is_top
            self.published_at = published_at
            self.id = nid

    def test_roundtrip(self):
        """v2 游标携带 sort：不携带 sort 时 hot 游标会被误解析为 published_at。"""
        n = self._N(1, "2026-10-07T10:00:00+00:00", "r_abc")
        assert _decode_cursor(_encode_cursor(n)) == (
            "latest",
            1,
            "2026-10-07T10:00:00+00:00",
            "r_abc",
        )

    def test_none_published_at(self):
        n = self._N(0, None, "n1")
        assert _decode_cursor(_encode_cursor(n)) == ("latest", 0, "", "n1")

    def test_v1_cursor_still_decodes(self):
        """升级前已下发到客户端的 v1 游标（3 元素）必须仍可用，否则老客户端翻页即崩。"""
        import base64
        import json

        raw = base64.urlsafe_b64encode(
            json.dumps([1, "2026-10-07T10:00:00+00:00", "r_abc"]).encode()
        ).decode()
        assert _decode_cursor(raw) == ("latest", 1, "2026-10-07T10:00:00+00:00", "r_abc")

    def test_unknown_sort_in_cursor_is_rejected(self):
        import base64
        import json

        raw = base64.urlsafe_b64encode(json.dumps([2, "bogus", 0, 1.0, "n1"]).encode()).decode()
        assert _decode_cursor(raw) is None

    def test_invalid_cursor(self):
        assert _decode_cursor("@@@not-base64@@@") is None
        assert _decode_cursor("") is None

    def test_wrong_shape(self):
        import base64
        import json

        raw = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode()
        assert _decode_cursor(raw) is None


class TestSchemaSingleTrack:
    """schema 由 Alembic 单轨管理：init_db 只校验、不建表。"""

    def test_init_db_does_not_create_tables(self):
        """空库上调用 init_db 不应建表，而应抛出带修复指引的错误。"""
        from sqlalchemy import create_engine, inspect

        import app.seed as seed

        eng = create_engine("sqlite:///:memory:", future=True)
        original = seed.engine
        seed.engine = eng
        try:
            with pytest.raises(RuntimeError) as exc:
                seed.init_db()
            message = str(exc.value)
            assert "alembic upgrade head" in message
            assert "缺少表" in message
            # 关键：没有就地建表
            assert not inspect(eng).has_table("news")
        finally:
            seed.engine = original

    def test_seed_source_has_no_create_all(self):
        """源码层面确认 init_db 不再包含 create_all（防回归）。"""
        import inspect as py_inspect

        import app.seed as seed

        src = py_inspect.getsource(seed.init_db)
        assert "create_all" not in src


class TestIndexesDeclared:
    """索引必须在模型中声明，否则 `alembic check` 判漂移、
    `--autogenerate` 会生成 drop_index 把迁移建的索引删掉。"""

    def test_feed_and_trgm_indexes_declared(self):
        from app.models import News

        names = {i.name for i in News.__table__.indexes}
        assert "ix_news_feed" in names
        assert "ix_news_title_trgm" in names
        assert "ix_news_summary_trgm" in names

    def test_feed_index_columns(self):
        from app.models import News

        feed = next(i for i in News.__table__.indexes if i.name == "ix_news_feed")
        assert [c.name for c in feed.columns] == [
            "channel",
            "is_top",
            "published_at",
            "id",
        ]
