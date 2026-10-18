"""P1-19：源健康历史与趋势。

覆盖四条线：
1. **记录**——每轮真实尝试一行，且写入失败不得影响抓取（旁路约束）；
2. **聚合**——窗口汇总、按天分桶、最近失败、过期清理、窗口收敛；
3. **两类隐形故障**——规律性抖动（只有历史看得见）、
   **成功但条数持续下滑**（当前健康字段结构性看不见的一类）；
4. **接口**——单源趋势端点与列表页近期成功率，含鉴权与 404。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.feeds import health as health_mod  # noqa: E402
from app.feeds import ingest as ingest_mod  # noqa: E402
from app.models import (  # noqa: E402
    RssSource,
    RssSourceHealthEvent,
    iso_utc,
    now_str,
)
from app.config import FEED_HEALTH_LIST_DAYS  # noqa: E402
from app.routers import admin as admin_mod  # noqa: E402

ADMIN_PHONE = "13800138000"

# 整点锚点：**5 天前的零点**。凡是涉及"按天分桶"的用例都从这个锚点往后推，
# 而不是"相对现在往前推 N 小时"——后者在跨午夜时（例如测试跑在 00:30）
# 会把事件甩到相邻日期上，同一天的两条记录被算成两天，凌晨跑就红。
BASE = (datetime.now(timezone.utc) - timedelta(days=5)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _admin_whitelist(monkeypatch):
    """与 `test_sources.py` 同一处理：`ADMIN_PHONES` 是模块级常量，
    靠 env 赋值会被"谁先被导入"决定。这里直接改模块绑定，测试间自动还原。"""
    monkeypatch.setattr(admin_mod, "ADMIN_PHONES", [ADMIN_PHONE])


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------- 工具


def _stamp(day: int = 4, hour: int = 0) -> datetime:
    """锚点起第 `day` 天的 `hour` 点（0..4，全部落在保留期内且必定早于现在）。

    D1-1：`at` 是 `DateTime`，**必须传 datetime 对象**——塞 ISO 串会被
    SQLite 直接拒（`DateTime type only accepts Python datetime`），PG 则是
    按会话时区隐式解释，两端行为都不一样。
    """
    return BASE + timedelta(days=day, hours=hour)


def _stamp_day(day: int = 4) -> str:
    """`_stamp` 所属的日期——分桶键就是 `at` 的前 10 位（UTC 日期）。"""
    return iso_utc(_stamp(day))[:10]


def _ago(days: float = 0, hours: float = 0) -> datetime:
    """N 天（+M 小时）前（aware UTC）。只用于**不涉及按天分桶**的用例。"""
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


def _event(db, key: str, ok: bool, count: int, *, at=None, days=0, hours=0,
           error=None, kind=None):
    row = RssSourceHealthEvent(
        source_key=key,
        ok=ok,
        item_count=count,
        error=error,
        kind=kind,
        at=at or _ago(days, hours),
    )
    db.add(row)
    db.commit()
    return row


def _source(db, key: str, url: str | None = None, **over) -> RssSource:
    row = RssSource(
        key=key,
        name=key,
        channel="tech",
        url=url or f"https://{key}.com/f.xml",
        weight=5,
        enabled=True,
        created_at=now_str(),
        updated_at=now_str(),
        **over,
    )
    db.add(row)
    db.commit()
    return row


def _count(db, key: str | None = None) -> int:
    q = select(func.count(RssSourceHealthEvent.id))
    if key:
        q = q.where(RssSourceHealthEvent.source_key == key)
    return int(db.execute(q).scalar() or 0)


def _admin(client) -> dict:
    client.post(
        "/api/auth/register",
        json={"phone": ADMIN_PHONE, "password": "abc123", "name": "管理员"},
    )
    r = client.post("/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _user(client, phone: str) -> dict:
    client.post("/api/auth/register", json={"phone": phone, "password": "abc123", "name": "路人"})
    r = client.post("/api/auth/login", json={"phone": phone, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


# ---------------------------------------------------------------- 记录


class TestRecordEvent:
    def test_success_and_failure_are_both_recorded(self):
        with SessionLocal() as db:
            _source(db, "s1")
            health_mod.record_event(db, "s1", True, 12)
            health_mod.record_event(db, "s1", False, 0, "HTTP 503", "transient")
            rows = db.execute(
                select(RssSourceHealthEvent).order_by(RssSourceHealthEvent.id)
            ).scalars().all()
            assert [(r.ok, r.item_count) for r in rows] == [(True, 12), (False, 0)]
            assert rows[1].error == "HTTP 503"
            assert rows[1].kind == "transient"

    def test_long_error_is_truncated_to_column_width(self):
        """错误原文可能很长（HTML 片段、堆栈）；列是 VARCHAR(300)。

        SQLite 不校验长度，所以本地永远绿、上线 PostgreSQL 才炸——
        这类差异必须在测试里显式钉住。
        """
        with SessionLocal() as db:
            _source(db, "s1")
            health_mod.record_event(db, "s1", False, 0, "E" * 500)
            row = db.execute(select(RssSourceHealthEvent)).scalars().one()
            assert len(row.error) == 300

    def test_blank_error_is_stored_as_null(self):
        """空串不是"没有错误"的好表达：前端 `v-if="error"` 对空串为真，
        会渲染出一个空白的错误条。"""
        with SessionLocal() as db:
            _source(db, "s1")
            health_mod.record_event(db, "s1", True, 3, "   ")
            row = db.execute(select(RssSourceHealthEvent)).scalars().one()
            assert row.error is None

    def test_write_failure_never_propagates(self):
        """这是旁路排障信息：写不进去也不能让本轮抓取失败（与告警、审计不同）。"""
        class BoomDB:
            def add(self, *_a, **_k):
                raise RuntimeError("库挂了")

            def commit(self):
                raise RuntimeError("库挂了")

            def rollback(self):
                pass

        health_mod.record_event(BoomDB(), "s1", True, 3)  # 不抛

    def test_prune_failure_returns_zero_instead_of_raising(self):
        class BoomDB:
            def execute(self, *_a, **_k):
                raise RuntimeError("库挂了")

            def commit(self):
                raise RuntimeError("库挂了")

            def rollback(self):
                pass

        assert health_mod.prune(BoomDB()) == 0


# ---------------------------------------------------------------- 窗口汇总


class TestSummarize:
    def test_empty_window_has_no_rate(self):
        """`rate` 为 None 而不是 0：**没抓过**与**抓了全挂**是两回事。

        前端据此才能区分「近期无数据」（源刚加 / 服务没跑）与「近期全失败」（要处理）。
        返回 0 会把前者伪装成后者，运营会去排查一个根本没跑过的源。
        """
        with SessionLocal() as db:
            _source(db, "s1")
            s = health_mod.summarize(db, "s1", 7)
            assert s["attempts"] == 0
            assert s["rate"] is None
            assert s["avgItems"] is None
            assert s["lastOkAt"] is None and s["lastFailAt"] is None

    def test_rate_over_mixed_attempts(self):
        with SessionLocal() as db:
            _source(db, "s1")
            for i in range(3):
                _event(db, "s1", True, 10, hours=i)
            _event(db, "s1", False, 0, hours=9, error="HTTP 500")
            s = health_mod.summarize(db, "s1", 7)
            assert (s["attempts"], s["ok"], s["failed"]) == (4, 3, 1)
            assert s["rate"] == 0.75

    def test_all_failed_rate_is_zero_not_none(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", False, 0, error="HTTP 404")
            s = health_mod.summarize(db, "s1", 7)
            assert s["rate"] == 0.0  # 不是 None

    def test_avg_items_counts_only_successful_rounds(self):
        """**成功轮次的平均条数**，不是全部轮次的。

        失败轮次天然是 0 条；混进来时"条数退化"会被失败率掩盖——
        例：3 次成功各 10/20/30 条 + 2 次失败，总平均 12，成功轮平均 20。
        """
        with SessionLocal() as db:
            _source(db, "s1")
            for n in (10, 20, 30):
                _event(db, "s1", True, n)
            _event(db, "s1", False, 0)
            _event(db, "s1", False, 0)
            s = health_mod.summarize(db, "s1", 7)
            assert s["avgItems"] == 20.0
            assert s["items"] == 60

    def test_last_ok_and_last_fail_are_tracked_separately(self):
        """"最后一次成功"和"最后一次失败"要分开：两个都是排障时的关键时间点。"""
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=3)
            _event(db, "s1", True, 5, days=2)
            _event(db, "s1", False, 0, days=1, error="HTTP 503")
            s = health_mod.summarize(db, "s1", 7)
            assert s["lastOkAt"] == iso_utc(_ago(2))
            assert s["lastFailAt"] == iso_utc(_ago(1))

    def test_events_outside_window_are_excluded(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=40)  # 超出 30 天保留期
            _event(db, "s1", True, 5, days=2)
            assert health_mod.summarize(db, "s1", 7)["attempts"] == 1
            assert health_mod.summarize(db, "s1", 60)["attempts"] == 2


# ---------------------------------------------------------------- 按天分桶


class TestDailyPoints:
    def test_buckets_by_day_in_ascending_order(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10, at=_stamp(0, 9))
            _event(db, "s1", False, 0, at=_stamp(2, 9), error="HTTP 500")
            _event(db, "s1", True, 8, at=_stamp(4, 21))
            points = health_mod.daily_points(db, "s1", 7)
            assert [p["date"] for p in points] == [_stamp_day(0), _stamp_day(2), _stamp_day(4)]
            assert points[0]["rate"] == 1.0
            assert points[1]["rate"] == 0.0

    def test_same_day_rounds_are_merged(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10, at=_stamp(1, 3))
            _event(db, "s1", False, 0, at=_stamp(1, 15))
            points = health_mod.daily_points(db, "s1", 7)
            assert len(points) == 1
            assert points[0]["date"] == _stamp_day(1)
            assert points[0]["total"] == 2 and points[0]["ok"] == 1
            assert points[0]["items"] == 10

    def test_missing_days_are_not_zero_filled(self):
        """**缺的天不补零**：空白 = "那天没抓它"（跳过 / 未启用 / 服务没跑）。

        补零会把它伪装成"抓了但全失败"——前者的处置是"为什么没跑"，
        后者是"去改配置"，完全不同。
        """
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10, at=_stamp(0, 9))
            _event(db, "s1", True, 10, at=_stamp(3, 9))
            points = health_mod.daily_points(db, "s1", 7)
            assert [p["date"] for p in points] == [_stamp_day(0), _stamp_day(3)]

    def test_no_events_yields_empty_list(self):
        with SessionLocal() as db:
            _source(db, "s1")
            assert health_mod.daily_points(db, "s1", 7) == []


# ---------------------------------------------------------------- 最近失败


class TestRecentFailures:
    def test_lists_only_failures_newest_first(self):
        """只列失败：成功在趋势图里已经看得见，这里要回答的是"它到底为什么挂"。"""
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10, at=_stamp(1))
            _event(db, "s1", False, 0, at=_stamp(2), error="旧失败")
            _event(db, "s1", False, 0, at=_stamp(4), error="新失败")
            f = health_mod.recent_failures(db, "s1", 7)
            assert [x["error"] for x in f] == ["新失败", "旧失败"]

    def test_limit_is_applied(self):
        with SessionLocal() as db:
            _source(db, "s1")
            for i in range(6):
                _event(db, "s1", False, 0, hours=i, error=f"E{i}")
            assert len(health_mod.recent_failures(db, "s1", 7, limit=3)) == 3

    def test_failure_nature_is_carried_through(self):
        """`kind` 决定前端标「永久性（要人工）」还是「可重试（等它自己好）」。"""
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", False, 0, error="HTTP 404", kind="permanent")
            assert health_mod.recent_failures(db, "s1", 7)[0]["kind"] == "permanent"

    def test_success_rounds_are_absent(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10)
            assert health_mod.recent_failures(db, "s1", 7) == []


# ---------------------------------------------------------------- 清理与窗口


class TestPrune:
    def test_deletes_only_events_older_than_keep_days(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=40)
            _event(db, "s1", True, 5, days=10)
            _event(db, "s1", True, 5, days=1)
            assert health_mod.prune(db) == 1
            assert _count(db) == 2

    def test_keep_days_argument_overrides_default(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=10)
            _event(db, "s1", True, 5, days=1)
            assert health_mod.prune(db, keep_days=5) == 1
            assert _count(db) == 1

    def test_nothing_to_prune_returns_zero(self):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5)
            assert health_mod.prune(db) == 0
            assert _count(db) == 1


class TestWindowDays:
    def test_defaults_to_keep_days(self):
        assert health_mod.window_days(None) == health_mod.FEED_HEALTH_KEEP_DAYS

    def test_beyond_keep_days_is_clamped(self):
        """要 90 天而只留 30 天：给 90 只会渲染出 60 个空格子。"""
        assert health_mod.window_days(90) == health_mod.FEED_HEALTH_KEEP_DAYS

    def test_within_keep_days_is_kept(self):
        assert health_mod.window_days(7) == 7

    def test_garbage_days_falls_back(self):
        for bad in (0, -1):
            assert health_mod.window_days(bad) == health_mod.FEED_HEALTH_KEEP_DAYS


# ---------------------------------------------------------------- 多源一次查


class TestSummarizeMany:
    def test_two_sources_in_one_pass(self):
        with SessionLocal() as db:
            _source(db, "a")
            _source(db, "b")
            _event(db, "a", True, 10)
            _event(db, "a", False, 0)
            _event(db, "b", True, 4)
            out = health_mod.summarize_many(db, ["a", "b"], 7)
            assert out["a"]["attempts"] == 2 and out["a"]["rate"] == 0.5
            assert out["b"]["attempts"] == 1 and out["b"]["rate"] == 1.0
            assert out["b"]["avgItems"] == 4.0

    def test_sources_without_events_are_absent(self):
        """没事件的源直接**不出现在结果里**，而不是塞一份全零摘要：
        调用方 `health.get(key)` 拿到 None，才能显示"近期无数据"而非"近期全挂"。"""
        with SessionLocal() as db:
            _source(db, "a")
            _source(db, "b")
            _event(db, "a", True, 10)
            out = health_mod.summarize_many(db, ["a", "b"], 7)
            assert "a" in out and "b" not in out

    def test_empty_key_list_short_circuits(self):
        with SessionLocal() as db:
            assert health_mod.summarize_many(db, [], 7) == {}


# ---------------------------------------------------------------- 抓取写入


class _Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class _ScriptedClient:
    """按 URL 片段返回固定响应；兼作上下文管理器（`collect` 里是 `with _client()`）。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, url, headers=None):
        self.calls.append(url)
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return _Resp(404, "")


RSS_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>{title}</title><link>{link}</link><description>正文</description>
  <pubDate>Mon, 07 Oct 2026 10:00:00 +0800</pubDate></item>
</channel></rss>
"""


class TestIngestWritesHistory:
    """写入口径：与「跳过的源不刷新健康状态」**同一条口径**。

    跳过的源本轮根本没发请求；给它记一行会伪造出"尝试过"的痕迹，
    趋势图上就会凭空多出一个失败点。
    """

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(ingest_mod.time, "sleep", lambda *_a: None)

    def _patch_http(self, monkeypatch, routes):
        monkeypatch.setattr(ingest_mod, "_client", lambda *_a, **_k: _ScriptedClient(routes))

    def test_every_attempted_source_writes_one_event(self, monkeypatch):
        self._patch_http(
            monkeypatch,
            {
                "ok.xml": _Resp(200, RSS_ONE.format(title="甲", link="l1")),
                "bad.xml": _Resp(404, ""),
            },
        )
        monkeypatch.setattr(ingest_mod, "FEED_MIN_INTERVAL_MINUTES", 0)
        with SessionLocal() as db:
            _source(db, "good", url="https://g.com/ok.xml")
            _source(db, "bad", url="https://b.com/bad.xml")
            ingest_mod.run_ingest(db, channel="tech", fallback_seed=False)

            assert _count(db, "good") == 1
            assert _count(db, "bad") == 1
            good = db.execute(
                select(RssSourceHealthEvent).where(RssSourceHealthEvent.source_key == "good")
            ).scalars().one()
            bad = db.execute(
                select(RssSourceHealthEvent).where(RssSourceHealthEvent.source_key == "bad")
            ).scalars().one()
            assert (good.ok, good.item_count) == (True, 1)
            assert bad.ok is False
            # 失败性质一并落库：趋势页要能标「永久性」而不是笼统的失败
            assert bad.error == "HTTP 404"
            assert bad.kind == "permanent"

    def test_interval_skipped_source_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(ingest_mod, "FEED_MIN_INTERVAL_MINUTES", 10)
        with SessionLocal() as db:
            _source(db, "recent", url="https://r.com/ok.xml", last_fetched_at=now_str())
            ingest_mod.run_ingest(db, channel="tech", fallback_seed=False)
            assert _count(db, "recent") == 0

    def test_circuit_skipped_source_writes_nothing(self, monkeypatch):
        """熔断期内的源本轮没发请求——记它就是在伪造失败。"""
        monkeypatch.setattr(ingest_mod, "FEED_MIN_INTERVAL_MINUTES", 0)
        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        with SessionLocal() as db:
            _source(
                db, "burnt", url="https://x.com/ok.xml",
                consecutive_failures=9, next_retry_at=future,
            )
            ingest_mod.run_ingest(db, channel="tech", fallback_seed=False)
            assert _count(db, "burnt") == 0


# ---------------------------------------------------------------- 两类隐形故障


class TestInvisibleFailures:
    """这两类故障是本批存在的理由：**当前健康字段结构性看不见它们**。"""

    def test_success_with_declining_items_is_visible(self):
        """一个源可以一直绿着，却在慢慢变空：5 轮全成功，条数 30 → 3。

        此时 `last_status='ok'`、`consecutive_failures=0`——现有字段口径下它**完全健康**，
        只有按天序列能看出它在退化。
        """
        with SessionLocal() as db:
            _source(db, "quiet", last_status="ok", consecutive_failures=0)
            for i, n in enumerate([30, 24, 18, 9, 3]):
                _event(db, "quiet", True, n, at=_stamp(i, 9))

            s = health_mod.summarize(db, "quiet", 30)
            assert s["rate"] == 1.0  # 成功率满分，老口径看不出任何问题
            assert s["attempts"] == 5 and s["failed"] == 0

            points = health_mod.daily_points(db, "quiet", 30)
            items = [p["items"] for p in points]
            assert items == [30, 24, 18, 9, 3]
            assert items == sorted(items, reverse=True)  # 严格单调下滑
            assert points[-1]["items"] == 3  # 最近一轮只剩 3 条

    def test_periodic_downtime_shows_up_as_a_pattern(self):
        """每天固定在同一时刻挂一次 = 对方的维护窗口，不是我们的锅。

        只看"最近一次失败"会以为它在随机抖动，从而去改配置；
        看出"失败集中在凌晨 3 点"之后，正确的处置是"不用管"。
        """
        with SessionLocal() as db:
            _source(db, "nightly")
            for d in range(5):
                _event(db, "nightly", False, 0, at=_stamp(d, 3), error="连接被重置")
                _event(db, "nightly", True, 20, at=_stamp(d, 15))

            points = health_mod.daily_points(db, "nightly", 30)
            assert [p["date"] for p in points] == [_stamp_day(d) for d in range(5)]
            # 每一天都是"成功 1 次 + 失败 1 次"——规律，而不是随机分布
            assert {(p["ok"], p["failed"]) for p in points} == {(1, 1)}

            s = health_mod.summarize(db, "nightly", 30)
            assert s["rate"] == 0.5  # 整体五成，任何单点口径都看不出"规律"二字
            # 规律本身：失败**全部**发生在同一个钟点。这条断言不受日期边界影响。
            assert {x["at"][11:13] for x in health_mod.recent_failures(db, "nightly", 30)} == {"03"}


# ---------------------------------------------------------------- 接口


class TestHealthEndpoint:
    def test_returns_summary_points_and_failures(self, client):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10, at=_stamp(1, 9))
            _event(db, "s1", False, 0, at=_stamp(3, 9), error="HTTP 503", kind="transient")
        r = client.get("/api/admin/sources/s1/health", headers=_admin(client))
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["key"] == "s1"
        assert d["summary"]["attempts"] == 2 and d["summary"]["rate"] == 0.5
        assert len(d["points"]) == 2
        assert d["failures"][0]["error"] == "HTTP 503"
        assert d["failures"][0]["kind"] == "transient"
        # 回传实际生效的天数：前端要标出"近 N 天"，否则百分比没有时间范围就是误导
        assert d["days"] == health_mod.FEED_HEALTH_KEEP_DAYS
        assert d["keepDays"] == health_mod.FEED_HEALTH_KEEP_DAYS

    def test_days_is_clamped_to_retention(self, client):
        with SessionLocal() as db:
            _source(db, "s1")
        r = client.get("/api/admin/sources/s1/health?days=120", headers=_admin(client))
        assert r.status_code == 200
        d = r.json()["data"]
        # 请求 120 天但只留 30 天：给 120 只会让前端铺 90 个空格子
        assert d["days"] == health_mod.FEED_HEALTH_KEEP_DAYS
        assert d["keepDays"] == health_mod.FEED_HEALTH_KEEP_DAYS

    def test_explicit_days_within_retention_is_honored(self, client):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=1)
            _event(db, "s1", True, 5, days=20)  # 落在 30 天窗口内、7 天窗口外
        r = client.get("/api/admin/sources/s1/health?days=7", headers=_admin(client))
        d = r.json()["data"]
        assert d["days"] == 7
        assert len(d["points"]) == 1

    def test_unknown_source_is_404(self, client):
        r = client.get("/api/admin/sources/nope/health", headers=_admin(client))
        assert r.status_code == 404
        assert r.json()["code"] == 40402

    def test_non_admin_is_rejected(self, client):
        with SessionLocal() as db:
            _source(db, "s1")
        r = client.get("/api/admin/sources/s1/health", headers=_user(client, "13700000001"))
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_anonymous_is_rejected(self, client):
        r = client.get("/api/admin/sources/s1/health")
        assert r.status_code == 401


class TestSourceListHealth:
    def test_list_carries_health_and_window_days(self, client):
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 10)
            _event(db, "s1", True, 20)
        r = client.get("/api/admin/sources", headers=_admin(client))
        d = r.json()["data"]
        row = next(x for x in d["list"] if x["key"] == "s1")
        assert row["health"]["attempts"] == 2
        assert row["health"]["avgItems"] == 15.0
        # 前端要标出"近 N 天"，否则 100% 这个数字没有时间范围就是误导
        assert d["healthDays"] == FEED_HEALTH_LIST_DAYS

    def test_source_without_history_has_null_health(self, client):
        """新加的源没有历史：`health` 为 null 而不是全零摘要——
        "还没抓过"和"抓了全挂"必须能在界面上区分开。"""
        with SessionLocal() as db:
            _source(db, "brandnew")
        r = client.get("/api/admin/sources", headers=_admin(client))
        row = next(x for x in r.json()["data"]["list"] if x["key"] == "brandnew")
        assert row["health"] is None

    def test_list_health_uses_shorter_window(self, client):
        """列表窗口（默认 7 天）短于趋势窗口（30 天）：列表回答"最近怎么样"，
        趋势回答"到底是什么时候开始变的"。"""
        with SessionLocal() as db:
            _source(db, "s1")
            _event(db, "s1", True, 5, days=20)  # 7 天窗口外
        r = client.get("/api/admin/sources", headers=_admin(client))
        row = next(x for x in r.json()["data"]["list"] if x["key"] == "s1")
        assert row["health"] is None
