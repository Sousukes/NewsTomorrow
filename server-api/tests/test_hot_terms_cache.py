"""D6：热搜词缓存的行为与边界。

热搜是一条 `GROUP BY term + ORDER BY count` 的聚合，每次打开搜索页都打一次，
而**窗口内结果对所有用户相同**。缓存它是最划算的一处（同样的昂贵计算被重复做）。

这里验四件事，其中第 2 条是本批最关键的坑：

1. 命中缓存时**不再查库**（用真实 SQL 事件计数，不看响应形状）；
2. **显式传 `now` 必须绕过缓存**——`now` 是测试/回放的注入点，缓存会让
   "换个时间点重算" 拿到上一份结果，测试之间互相污染，且症状像"聚合算错了"；
3. `days` / `limit` 不同的组合**不能共用一个键**（否则 7 天榜和 30 天榜串数据）；
4. 缓存关闭时结果仍正确（缓存只影响性能，不影响正确性）。

运行：
    cd server-api
    pytest tests/test_hot_terms_cache.py -q
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import config  # noqa: E402
from app import search_stats  # noqa: E402
from app.models import Base, SearchLog  # noqa: E402

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _enable_cache(monkeypatch):
    """conftest 把 `CACHE_ENABLED` 钉成 false（让用例每次真算）。

    这里要测缓存本身，所以在**运行期**改模块属性——`TTLCache.get/set` 是调用时
    读 `config.CACHE_ENABLED`，所以 monkeypatch 生效。
    """
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    search_stats._hot_cache.clear()
    yield
    search_stats._hot_cache.clear()


_next_id = {"v": 1}


def _seed(db, terms: list[tuple[str, int]]) -> None:
    """造搜索日志。

    `search_logs.id` 在 SQLite 上**没有自增默认值**（模型只声明
    `Integer, primary_key=True`，SQLite 不会为此建 AUTOINCREMENT），
    所以必须显式给 id——否则整批 INSERT 直接撞 NOT NULL。
    """
    for term, n in terms:
        for i in range(n):
            _next_id["v"] += 1
            db.add(
                SearchLog(
                    id=_next_id["v"],
                    term=term,
                    user_id=None,
                    created_at=(NOW - timedelta(hours=i)).isoformat(timespec="seconds"),
                )
            )
    db.commit()


def _count_selects(db) -> int:
    """统计本会话执行过的 SELECT 次数（真 SQL，不看响应形状）。"""
    counter = {"n": 0}

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    sa.event.listen(db.bind, "before_cursor_execute", before)
    db._select_probe = counter  # type: ignore[attr-defined]
    return counter["n"]


@pytest.fixture
def select_counter(db):
    counter = {"n": 0}

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    sa.event.listen(db.bind, "before_cursor_execute", before)
    yield counter
    sa.event.remove(db.bind, "before_cursor_execute", before)


class TestCacheHit:
    def test_second_call_does_not_query(self, db, select_counter):
        _seed(db, [("甲", 3), ("乙", 1)])
        first = search_stats.hot_terms(db, limit=10, days=7)
        after_first = select_counter["n"]
        second = search_stats.hot_terms(db, limit=10, days=7)

        assert first == second
        assert after_first > 0, "第一次应当真的查了库"
        assert select_counter["n"] == after_first, "第二次应命中缓存，不再产生 SQL"

    def test_cached_result_is_the_same_object_shape(self, db):
        _seed(db, [("甲", 2)])
        first = search_stats.hot_terms(db, limit=5, days=7)
        second = search_stats.hot_terms(db, limit=5, days=7)
        assert second == first
        assert all(set(r) == {"term", "count"} for r in second)

    def test_empty_result_is_cached_too(self, db, select_counter):
        """空榜也缓存：新站/低流量时每次都跑聚合同样是浪费。"""
        first = search_stats.hot_terms(db, limit=10, days=7)
        n = select_counter["n"]
        second = search_stats.hot_terms(db, limit=10, days=7)
        assert first == [] and second == []
        assert select_counter["n"] == n


class TestNowBypassesCache:
    def test_explicit_now_always_queries(self, db, select_counter):
        """**本批最关键的一条**：显式 `now` 不能被缓存短路。

        `now` 是测试与回放的注入点。若缓存生效，"用另一个时间点重算" 会拿到
        上一份结果——测试之间互相污染，而症状看起来像"聚合算错了"。
        """
        _seed(db, [("甲", 2)])
        a = search_stats.hot_terms(db, limit=10, days=7, now=NOW)
        n1 = select_counter["n"]
        b = search_stats.hot_terms(db, limit=10, days=7, now=NOW)
        assert a == b
        assert select_counter["n"] > n1, "显式 now 必须绕过缓存，每次都真算"

    def test_explicit_now_sees_data_added_after_cached_call(self, db):
        """先缓存一次（不带 now），再用带 now 的调用能看到新数据。"""
        _seed(db, [("甲", 1)])
        search_stats.hot_terms(db, limit=10, days=7)  # 写入缓存
        _seed(db, [("新词", 5)])
        fresh = search_stats.hot_terms(db, limit=10, days=7, now=NOW)
        assert any(r["term"] == "新词" for r in fresh)


class TestKeyIsolation:
    def test_different_days_do_not_share_a_key(self, db):
        _seed(db, [("甲", 2)])
        a = search_stats.hot_terms(db, limit=10, days=7)
        b = search_stats.hot_terms(db, limit=10, days=30)
        # 两者都只含"甲"，但**必须是分别算出来的**（键不同）
        assert a == b
        assert search_stats._hot_cache.get("hot_terms:7:10") is not None
        assert search_stats._hot_cache.get("hot_terms:30:10") is not None

    def test_different_limit_does_not_share_a_key(self, db):
        _seed(db, [("甲", 3), ("乙", 2), ("丙", 1)])
        top1 = search_stats.hot_terms(db, limit=1, days=7)
        top3 = search_stats.hot_terms(db, limit=3, days=7)
        assert len(top1) == 1
        assert len(top3) == 3, "limit=1 的结果不能串到 limit=3 上"


class TestCacheDisabled:
    def test_disabled_still_returns_correct_data(self, db, select_counter, monkeypatch):
        """缓存关闭只影响性能，不影响正确性——这条防止"关缓存后功能坏掉"。"""
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        _seed(db, [("甲", 2)])
        a = search_stats.hot_terms(db, limit=10, days=7)
        n = select_counter["n"]
        b = search_stats.hot_terms(db, limit=10, days=7)
        assert a == b
        assert select_counter["n"] > n, "关闭缓存后每次都应真算"


class TestOrderingStable:
    def test_same_count_orders_by_term(self, db):
        """同次数按词升序——顺序不稳会让前端榜单每次刷新都抖动。"""
        _seed(db, [("b", 1), ("a", 1), ("c", 1)])
        out = search_stats.hot_terms(db, limit=10, days=7, now=NOW)
        assert [r["term"] for r in out] == ["a", "b", "c"]
