"""B2 + B3：资讯列表热路径的性能债务。

两个断言风格，都是「结构上不可能被悄悄改回去」的写法：

- **B2** 用 SQLAlchemy 事件抓真实执行的语句，断言默认路径**不出现 COUNT**。
  只断言返回值形状是不够的——`total` 改成 `null` 但底下照样 `count()`，
  响应契约看不出差别，性能债原封不动地留着；
- **B3** 数「聚合查询执行了几次」，断言第二次 `for-you` 请求不再打库。
  同样：不看 SQL 就没法证明缓存真的生效了。

运行：
    cd server-api
    pytest tests/test_list_perf.py -q
"""
import os

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
os.environ["HOT_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

import main  # noqa: E402
from app import cache as cache_mod  # noqa: E402
from app import config  # noqa: E402
from app import ranking  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News, now_str  # noqa: E402

PASSWORD = "list-perf-pass"


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        for i in range(30):
            db.add(
                News(
                    id=f"p{i:02d}",
                    title=f"性能测试条目 {i}",
                    source="测试源",
                    channel="tech",
                    published_at=f"2026-10-18T00:{i:02d}:00+00:00",
                    hot_score=float(30 - i),
                )
            )
        db.commit()
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def statements():
    """抓本次作用域内执行的 SQL（表名化，便于断言）。"""
    captured: list[str] = []

    def on_execute(conn, cursor, statement, params, context, executemany):
        captured.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)


def _login(client, phone: str = "13800138999") -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": PASSWORD, "name": "测试"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


class TestB2KeysetByDefault:
    def test_default_path_runs_no_count(self, client, statements):
        """**B2 的核心断言**：默认请求里不得出现 COUNT 语句。

        大表上 `count()` 是全表扫描；首页 100% 走这条路径。
        """
        client.get("/api/news?pageSize=10")
        counts = [s for s in statements if s.upper().startswith("SELECT COUNT")]
        assert not counts, f"默认路径仍在全表 count：{counts}"

    def test_legacy_page_path_still_counts(self, client, statements):
        """兼容路径按需 count——保留「第 N 页 / 总条数」语义。"""
        client.get("/api/news?page=1&pageSize=10")
        assert any(s.upper().startswith("SELECT COUNT") for s in statements)

    def test_default_first_page_shape(self, client):
        d = client.get("/api/news?pageSize=10").json()["data"]
        assert d["total"] is None
        assert d["page"] is None
        assert d["hasMore"] is True
        assert d["nextCursor"]

    def test_cursor_paging_walks_all_items_without_overlap(self, client):
        """keyset 首屏 + 续页：不重不漏（深分页也不需要 offset）。"""
        seen: list[str] = []
        cursor = None
        for _ in range(5):
            url = "/api/news?pageSize=10" + (f"&cursor={cursor}" if cursor else "")
            d = client.get(url).json()["data"]
            seen.extend(n["id"] for n in d["list"])
            cursor = d.get("nextCursor")
            if not cursor:
                break
        assert len(seen) == 30
        assert len(set(seen)) == 30

    def test_explicit_page_wins_over_keyset_shape(self, client):
        """同时给 page 与 cursor：cursor 优先（续页语义），page 不触发 count。"""
        first = client.get("/api/news?pageSize=10").json()["data"]
        d = client.get(f"/api/news?pageSize=10&page=1&cursor={first['nextCursor']}").json()["data"]
        assert d["total"] is None
        assert d["page"] is None


class TestB3ChannelPrefCache:
    @pytest.fixture
    def cached(self, monkeypatch):
        """打开缓存（根 conftest 钉 false），并给一个短的 TTL 以便验证过期。"""
        monkeypatch.setattr(config, "CACHE_ENABLED", True)
        ranking._pref_cache.clear()
        monkeypatch.setattr(ranking._pref_cache, "default_ttl", 300)
        yield
        ranking._pref_cache.clear()

    @staticmethod
    def _group_by_statements(statements) -> list[str]:
        """偏好计算的那两条聚合：JOIN ... GROUP BY news.channel"""
        return [s for s in statements if "GROUP BY news.channel" in s]

    def test_second_for_you_request_hits_no_db(self, client, statements, cached):
        h = _login(client)
        # 先有行为数据：空偏好**按设计不缓存**（多数访客属于这一类），
        # 拿空偏好去测缓存命中，测的是另一条分支
        client.post("/api/me/favorites", json={"newsId": "p00"}, headers=h)
        client.post("/api/me/history", json={"newsId": "p01"}, headers=h)

        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        first_round = len(self._group_by_statements(statements))

        before = len(statements)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        # 第二次仍会跑列表查询与鉴权查用户——那不是"重复计算"。
        # 该断言的是**偏好那两条聚合**不再出现。
        assert first_round == 2, "首次请求应当算出两条聚合（收藏 + 浏览）"
        assert (
            self._group_by_statements(statements[before:]) == []
        ), "缓存未生效，第二次仍在算频道偏好聚合"

    def test_favorite_invalidates_cache(self, client, statements, cached):
        """失效优先于过期：刚收藏完就该看到 for-you 排序变化。"""
        h = _login(client)
        client.post("/api/me/favorites", json={"newsId": "p00"}, headers=h)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)

        before = len(statements)
        client.post("/api/me/favorites", json={"newsId": "p01"}, headers=h)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        # 关键断言：收藏之后确实重算了（而不是继续用旧权重）
        assert self._group_by_statements(statements[before:]), "收藏后没有触发偏好重算（缓存未失效）"

    def test_history_write_invalidates_cache(self, client, statements, cached):
        h = _login(client)
        client.post("/api/me/history", json={"newsId": "p02"}, headers=h)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)

        before = len(statements)
        client.post("/api/me/history", json={"newsId": "p03"}, headers=h)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        assert self._group_by_statements(statements[before:]), "浏览记录后没有触发偏好重算"

    def test_empty_preferences_not_cached(self, client, statements, cached):
        """无行为数据的访客不产生缓存条目：多数访客属于这一类。"""
        h = _login(client)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        # 直接查缓存内部：不该有任何 chanpref 键
        internal = getattr(ranking._pref_cache, "_data", {})
        assert not any(str(k).startswith("chanpref:") for k in internal)

    def test_cache_disabled_always_recomputes(self, client, statements):
        """开关关掉时（测试环境默认）行为与从前一致：每次都算。"""
        h = _login(client)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        before = len(statements)
        client.get("/api/news?sort=for-you&pageSize=5", headers=h)
        assert len(self._group_by_statements(statements[before:])) == 2


class TestCacheModule:
    def test_ttl_expiry(self, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", True)
        c = cache_mod.TTLCache(default_ttl=1)
        c.set("k", {"a": 1})
        assert c.get("k") == {"a": 1}
        monkeypatch.setattr(cache_mod.time, "time", lambda: 9_999_999_999)
        assert c.get("k") is None

    def test_capacity_evicts(self, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", True)
        c = cache_mod.TTLCache(default_ttl=300, max_keys=3)
        for i in range(10):
            c.set(f"k{i}", i)
        assert len(c._data) <= 3

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        c = cache_mod.TTLCache(default_ttl=300)
        c.set("k", 1)
        assert c.get("k") is None