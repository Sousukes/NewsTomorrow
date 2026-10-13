"""P1-16：抓取链路治理——并发化 / 礼貌抓取 / 超时告警。

三条线：
1. **并发但不失序**：多源并行抓取省掉等待时间，但合并仍按权重顺序串行进行——
   "多源转载同一稿件时权重高者先入库"是先到先得，并发合并会把它随机化；
2. **礼貌抓取**：按 host 分组（同站串行 + 间隔）、单源最小抓取间隔、
   robots.txt 遵守且逐源可关（取不到 robots 时放行）；
3. **超时告警**：异常源与超时未成功源主动通知管理员，并按 `last_alerted_at` 节流。
"""
import os
import threading

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.feeds import alert as alert_mod  # noqa: E402
from app.feeds import fetcher  # noqa: E402
from app.feeds.ingest import _fetch_one, _group_by_host, _too_soon, collect  # noqa: E402
from app.models import AdminUser, Message, RssSource, User, now_str  # noqa: E402

RSS_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>{title}</title><link>{link}</link><description>正文</description>
  <pubDate>Mon, 07 Oct 2026 10:00:00 +0800</pubDate></item>
</channel></rss>
"""
ROBOTS_DENY = "User-agent: *\nDisallow: /\n"


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class ScriptedClient:
    """按 URL 片段返回不同响应；记录调用轨迹与**并发峰值**（用于验证同站串行）。"""

    def __init__(self, routes: dict, robots: str | None = None):
        self.routes = routes
        self.robots = robots
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._inflight = 0
        self.peak = 0

    def get(self, url, headers=None):
        with self._lock:
            self.calls.append(url)
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
        try:
            return self._respond(url)
        finally:
            with self._lock:
                self._inflight -= 1

    def _respond(self, url):
        if url.endswith("/robots.txt"):
            return Resp(200, self.robots) if self.robots else Resp(404, "")
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return Resp(404, "")


def _src(key: str, url: str, weight: int = 5, **over) -> dict:
    base = {
        "key": key,
        "name": key,
        "channel": "tech",
        "url": url,
        "weight": weight,
        "respect_robots": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- 分组与礼仪判定


class TestGrouping:
    def test_group_by_host_preserves_weight_order(self):
        srcs = [
            _src("a", "https://x.com/1.xml", 9),
            _src("b", "https://y.com/1.xml", 8),
            _src("c", "https://x.com/2.xml", 7),
        ]
        groups = _group_by_host(srcs)
        assert [g[0]["key"] for g in groups] == ["a", "b"]  # 组按首个源出现顺序
        assert [s["key"] for s in groups[0]] == ["a", "c"]  # 组内保持相对顺序

    def test_different_hosts_are_separate_groups(self):
        srcs = [_src("a", "https://x.com/1.xml"), _src("b", "https://y.com/1.xml")]
        assert len(_group_by_host(srcs)) == 2

    def test_too_soon_uses_last_fetched_at(self):
        recent = {"last_fetched_at": (datetime.now() - timedelta(minutes=2)).isoformat()}
        assert _too_soon(recent, 10) is True
        assert _too_soon({"last_fetched_at": None}, 10) is False
        old = {"last_fetched_at": (datetime.now() - timedelta(minutes=30)).isoformat()}
        assert _too_soon(old, 10) is False


# ---------------------------------------------------------------- 并发与合并顺序


class TestConcurrentCollect:
    def test_higher_weight_source_wins_on_duplicate(self):
        """同一稿件两源都返回：合并后必须留下高权重源那条（并发不得改变入库优先级）。"""
        feed = RSS_ONE.format(title="同一篇", link="http://same/1")
        client = ScriptedClient({"hi.xml": Resp(200, feed), "lo.xml": Resp(200, feed)})
        items = collect(
            sources=[_src("hi", "https://a.com/hi.xml", 10), _src("lo", "https://b.com/lo.xml", 1)],
            client=client,
            max_concurrency=2,
            host_gap=0,
            min_interval_minutes=0,
        )
        assert len(items) == 1  # 同 id 去重
        assert items[0].source == "hi"

    def test_stats_follow_source_order_not_completion_order(self):
        """stats 顺序 = 权重序：健康状态回写与审计顺序不应随网络快慢漂移。"""
        stats: list[dict] = []
        srcs = [
            _src("first", "https://unreachable.invalid/a.xml", 10),
            _src("second", "https://unreachable.invalid/b.xml", 1),
        ]
        out = collect(
            sources=srcs, stats=stats, client=ScriptedClient({}),
            max_concurrency=2, host_gap=0, min_interval_minutes=0,
        )
        assert out == []
        assert [s["key"] for s in stats] == ["first", "second"]
        assert all(s["ok"] is False for s in stats)

    def test_same_host_never_exceeds_one_inflight(self):
        """同一站点的多个源串行抓取：并发峰值恒为 1（礼貌的硬约束）。"""
        client = ScriptedClient({"1.xml": Resp(200, RSS_ONE.format(title="a", link="l1")),
                                 "2.xml": Resp(200, RSS_ONE.format(title="b", link="l2"))})
        collect(
            sources=[_src("a", "https://same.com/1.xml", 5), _src("b", "https://same.com/2.xml", 4)],
            client=client, max_concurrency=4, host_gap=0, min_interval_minutes=0,
        )
        assert client.peak == 1
        assert len(client.calls) == 2

    def test_different_hosts_do_run_in_parallel(self):
        """不同站点应当并行：串行实现下并发峰值只能是 1。

        用可控的 sleep 制造重叠窗口——这是"并发化"这条需求唯一有意义的断言。
        """
        import time as _t

        class SlowClient(ScriptedClient):
            def _respond(self, url):
                # 只延长**响应**耗时：计数已在父类 get() 里加减，
                # 若在这里之外 sleep，两个请求根本不会重叠在计数窗口内（断言会假通过/假失败）
                _t.sleep(0.15)
                return super()._respond(url)

        client = SlowClient({"a.xml": Resp(200, RSS_ONE.format(title="a", link="la")),
                             "b.xml": Resp(200, RSS_ONE.format(title="b", link="lb"))})
        collect(
            sources=[_src("a", "https://h1.com/a.xml", 5), _src("b", "https://h2.com/b.xml", 4)],
            client=client, max_concurrency=4, host_gap=0, min_interval_minutes=0,
        )
        assert client.peak == 2  # 两个站点同时在线


# ---------------------------------------------------------------- robots 与最小间隔


class TestPoliteness:
    def setup_method(self):
        fetcher.clear_robots_cache()

    def test_robots_denied_is_error_without_fetching_feed(self):
        client = ScriptedClient({"feed.xml": Resp(200, RSS_ONE.format(title="t", link="l"))},
                                robots=ROBOTS_DENY)
        key, items, err, skipped = _fetch_one(
            client, _src("denied", "https://denied.com/feed.xml", respect_robots=True), 10, 0
        )
        assert err == "robots.txt 禁止抓取"
        assert items is None
        assert skipped is False
        assert not any("feed.xml" in c for c in client.calls)

    def test_robots_missing_is_fail_open(self):
        """取不到 robots（404）不应阻断抓取：没有声明不等于禁止。"""
        client = ScriptedClient({"feed.xml": Resp(200, RSS_ONE.format(title="t", link="l"))})
        key, items, err, skipped = _fetch_one(
            client, _src("ok", "https://ok.com/feed.xml", respect_robots=True), 10, 0
        )
        assert err is None and len(items) == 1

    def test_respect_robots_can_be_disabled_per_source(self):
        """逐源开关：关掉后即使 robots 禁止也照抓（应对写得过严却公开提供 RSS 的源）。"""
        client = ScriptedClient({"feed.xml": Resp(200, RSS_ONE.format(title="t", link="l"))},
                                robots=ROBOTS_DENY)
        key, items, err, skipped = _fetch_one(
            client, _src("override", "https://override.com/feed.xml", respect_robots=False), 10, 0
        )
        assert err is None and len(items) == 1

    def test_robots_is_cached_per_host(self):
        """robots 是整站声明：同站多源只取一次（取本身就是一次请求）。"""
        client = ScriptedClient({"1.xml": Resp(200, RSS_ONE.format(title="a", link="l1")),
                                 "2.xml": Resp(200, RSS_ONE.format(title="b", link="l2"))},
                                robots="User-agent: *\nAllow: /\n")
        for i in (1, 2):
            _fetch_one(client, _src(f"s{i}", f"https://cache.com/{i}.xml",
                                    respect_robots=True), 10, 0)
        assert sum(1 for c in client.calls if c.endswith("/robots.txt")) == 1

    def test_min_interval_skips_without_any_request(self):
        client = ScriptedClient({})
        src = _src("recent", "https://recent.com/f.xml",
                   last_fetched_at=datetime.now().isoformat(timespec="seconds"))
        key, items, err, skipped = _fetch_one(client, src, 10, 10)
        assert skipped is True
        assert client.calls == []


# ---------------------------------------------------------------- 超时告警


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _source(db, key, *, status="ok", fetched_minutes_ago=1.0, enabled=True,
            alerted_minutes_ago=None):
    last = (
        (datetime.now() - timedelta(minutes=fetched_minutes_ago)).isoformat(timespec="seconds")
        if fetched_minutes_ago is not None else None
    )
    row = RssSource(
        key=key, name=key, channel="tech", url=f"https://{key}.com/f.xml",
        weight=5, enabled=enabled, last_status=status,
        last_error="连接超时" if status == "error" else None,
        last_fetched_at=last, created_at=now_str(), updated_at=now_str(),
    )
    if alerted_minutes_ago is not None:
        row.last_alerted_at = (
            datetime.now() - timedelta(minutes=alerted_minutes_ago)
        ).isoformat(timespec="seconds")
    db.add(row)
    db.commit()
    return row


def _admin(db, phone="13800138000"):
    u = User(phone=phone, password_hash="x", name="管理员", created_at=now_str())
    db.add(u)
    db.add(AdminUser(phone=phone, note="", added_by=phone, created_at=now_str()))
    db.commit()
    return u


class TestSourceAlerts:
    def test_error_source_is_unhealthy(self, db):
        _source(db, "bad", status="error")
        items = alert_mod.unhealthy_sources(db)
        assert [i["key"] for i in items] == ["bad"]
        assert "抓取失败" in items[0]["reason"]

    def test_never_fetched_is_unhealthy(self, db):
        _source(db, "fresh", fetched_minutes_ago=None)
        assert [i["key"] for i in alert_mod.unhealthy_sources(db)] == ["fresh"]

    def test_stale_is_unhealthy(self, db):
        _source(db, "old", fetched_minutes_ago=60 * 48)  # 48 小时前
        items = alert_mod.unhealthy_sources(db, stale_hours=24)
        assert [i["key"] for i in items] == ["old"]
        assert "未成功抓取" in items[0]["reason"]

    def test_healthy_and_recent_is_not_reported(self, db):
        _source(db, "good", fetched_minutes_ago=5)
        assert alert_mod.unhealthy_sources(db) == []

    def test_disabled_source_is_not_reported(self, db):
        """禁用的源不抓是预期行为，不该告警。"""
        _source(db, "off", status="error", enabled=False)
        assert alert_mod.unhealthy_sources(db) == []

    def test_notifies_admin_users(self, db):
        admin = _admin(db)
        _source(db, "bad", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        msg = db.execute(select(Message)).scalars().one()
        assert msg.user_id == admin.id
        assert msg.type == "system"
        assert "bad" in msg.summary

    def test_throttled_within_window(self, db):
        """节流：窗口内刚告警过的源不重复打扰；窗口外重新告警。"""
        _admin(db)
        row = _source(db, "bad", status="error", alerted_minutes_ago=10)
        assert alert_mod.notify_unhealthy_sources(db) == 0
        row.last_alerted_at = (datetime.now() - timedelta(hours=8)).isoformat(timespec="seconds")
        db.commit()
        assert alert_mod.notify_unhealthy_sources(db) == 1

    def test_no_recipients_keeps_anchor_unset(self, db):
        """没有已注册管理员：不报错，且**不推进**节流锚点——

        没通知到人就不该占用窗口，否则管理员一注册得白等一个节流周期才收到告警。
        """
        _source(db, "bad", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 0
        assert db.get(RssSource, "bad").last_alerted_at is None
        # 一旦管理员出现，应能立刻收到告警（锚点未被占用）
        _admin(db)
        assert alert_mod.notify_unhealthy_sources(db) == 1

    def test_env_only_admin_is_also_a_recipient(self, monkeypatch, db):
        """env 白名单（未入库的应急管理员）若已注册，同样应收到告警。"""
        monkeypatch.setattr(alert_mod, "ADMIN_PHONES", ["13900000123"])
        u = User(phone="13900000123", password_hash="x", name="应急", created_at=now_str())
        db.add(u)
        db.commit()
        _source(db, "bad", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        assert db.execute(select(Message)).scalars().one().user_id == u.id

    def test_unhealthy_count_metric(self, db):
        _source(db, "bad1", status="error")
        _source(db, "bad2", fetched_minutes_ago=None)
        _source(db, "good", fetched_minutes_ago=5)
        assert alert_mod.unhealthy_count(db) == 2
