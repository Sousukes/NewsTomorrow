"""P1-16：抓取链路治理——并发化 / 礼貌抓取 / 超时告警。

三条线：
1. **并发但不失序**：多源并行抓取省掉等待时间，但合并仍按权重顺序串行进行——
   "多源转载同一稿件时权重高者先入库"是先到先得，并发合并会把它随机化；
2. **礼貌抓取**：按 host 分组（同站串行 + 间隔）、单源最小抓取间隔、
   robots.txt 遵守且逐源可关（取不到 robots 时放行）；
3. **超时告警**：异常源与超时未成功源主动通知管理员，并按 `last_alerted_at` 节流。

P1-17 追加：告警**去重聚合**——待告警源达到阈值时合并成一条汇总（按原因分组），
避免断网时十几个源一起刷屏；见 `TestAlertAggregation`。
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
from app.feeds import ingest as ingest_mod  # noqa: E402
from app.feeds import sources as src_mod  # noqa: E402
from app.feeds.ingest import (  # noqa: E402
    SKIP_CIRCUIT,
    SKIP_INTERVAL,
    _circuit_open,
    _fetch_one,
    _group_by_host,
    _too_soon,
    collect,
)
from app.models import (  # noqa: E402
    AdminAuditLog,
    AdminUser,
    Message,
    RssSource,
    User,
    now_str,
)

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
        out = _fetch_one(
            client, _src("denied", "https://denied.com/feed.xml", respect_robots=True), 10, 0
        )
        assert out.error == "robots.txt 禁止抓取"
        assert out.items is None
        assert out.skipped is False
        # robots 拒绝是**永久性**的：重试多少次对方都不会突然允许
        assert out.kind == fetcher.PERMANENT
        assert not any("feed.xml" in c for c in client.calls)

    def test_robots_missing_is_fail_open(self):
        """取不到 robots（404）不应阻断抓取：没有声明不等于禁止。"""
        client = ScriptedClient({"feed.xml": Resp(200, RSS_ONE.format(title="t", link="l"))})
        out = _fetch_one(
            client, _src("ok", "https://ok.com/feed.xml", respect_robots=True), 10, 0
        )
        assert out.error is None and len(out.items) == 1

    def test_respect_robots_can_be_disabled_per_source(self):
        """逐源开关：关掉后即使 robots 禁止也照抓（应对写得过严却公开提供 RSS 的源）。"""
        client = ScriptedClient({"feed.xml": Resp(200, RSS_ONE.format(title="t", link="l"))},
                                robots=ROBOTS_DENY)
        out = _fetch_one(
            client, _src("override", "https://override.com/feed.xml", respect_robots=False), 10, 0
        )
        assert out.error is None and len(out.items) == 1

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
        out = _fetch_one(client, src, 10, 10)
        assert out.skipped is True
        assert out.skip_reason == SKIP_INTERVAL  # 与熔断跳过区分，便于日志解释
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
            alerted_minutes_ago=None, name=None, weight=5,
            fail_kind=None, failures=0):
    last = (
        (datetime.now() - timedelta(minutes=fetched_minutes_ago)).isoformat(timespec="seconds")
        if fetched_minutes_ago is not None else None
    )
    row = RssSource(
        key=key, name=name or key, channel="tech", url=f"https://{key}.com/f.xml",
        weight=weight, enabled=enabled, last_status=status,
        last_error="连接超时" if status == "error" else None,
        last_fetched_at=last, created_at=now_str(), updated_at=now_str(),
        failure_kind=fail_kind, consecutive_failures=failures,
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


class TestAlertAggregation:
    """P1-17：多源同时异常时合并成一条汇总，而不是一源一条刷屏。

    断网 / 对方站点整体挂掉时十几个源会一起异常，逐源发一条等于把同一个坏消息
    重复 N 遍；收件人对 N 条独立消息也做不了逐个处置。
    """

    def test_single_source_stays_direct(self, db):
        """1 条时汇总反而更绕，保持逐源直发（点开就是名字）。"""
        _admin(db)
        _source(db, "bad", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        msg = db.execute(select(Message)).scalars().one()
        assert msg.title == "信源异常提醒"
        assert "bad" in msg.summary

    def test_below_threshold_is_not_aggregated(self, db):
        """阈值以下（默认 3）仍逐源：一两条时逐个读名字更省事。"""
        _admin(db)
        _source(db, "a", status="error")
        _source(db, "b", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 2
        assert {m.title for m in db.execute(select(Message)).scalars()} == {"信源异常提醒"}

    def test_above_threshold_collapses_to_one_per_recipient(self, db):
        """5 个源 × 2 个管理员 = 2 条汇总，而不是 10 条。"""
        _admin(db, "13800138000")
        _admin(db, "13800138001")
        for k in "abcde":
            _source(db, k, status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 2
        msgs = db.execute(select(Message)).scalars().all()
        assert len({m.user_id for m in msgs}) == 2
        assert all(m.title == "信源异常汇总（5 条）" for m in msgs)

    def test_batch_summary_groups_by_cause_and_severity(self, db):
        """按原因分组、抓取失败排最前：预览是单行省略号，最严重必须最先被看到。"""
        _admin(db)
        _source(db, "e1", status="error")
        _source(db, "e2", status="error")
        _source(db, "s1", fetched_minutes_ago=None)  # 从未成功
        _source(db, "s2", fetched_minutes_ago=60 * 48)  # 超时未成功
        alert_mod.notify_unhealthy_sources(db)
        summary = db.execute(select(Message)).scalars().one().summary
        assert summary.index("抓取失败") < summary.index("超时未抓取")
        assert "抓取失败 2 条" in summary
        assert "超时未抓取 1 条" in summary
        assert "从未成功 1 条" in summary

    def test_all_stale_surfaces_shared_cause(self, db):
        """全是"从未成功"= 系统性问题（定时任务/网络），分组本身就把这层信息带出来了，
        不需要再写一句"建议排查网络"——那句话大概率被省略号吃掉。"""
        _admin(db)
        for k in "abcd":
            _source(db, k, fetched_minutes_ago=None)
        alert_mod.notify_unhealthy_sources(db)
        summary = db.execute(select(Message)).scalars().one().summary
        assert "从未成功 4 条" in summary
        assert "抓取失败" not in summary

    def test_long_name_list_is_elided(self, db):
        """超过列举上限时只列前 N 个并用"等"概括，但总量必须准确。"""
        _admin(db)
        for i in range(8):
            _source(db, f"s{i}", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        s = db.execute(select(Message)).scalars().one().summary
        assert "抓取失败 8 条" in s
        assert "等" in s
        assert s.count("、") == 4  # 列了 5 个名字

    def test_identical_truncated_names_are_deduped(self, db):
        """名字过长被截成同一前缀时并列出来是纯噪音——去重，但**总量不变**。"""
        _admin(db)
        for i in range(4):
            _source(db, f"d{i}", status="error", name="同一站点的超长频道名称" * 3)
        alert_mod.notify_unhealthy_sources(db)
        s = db.execute(select(Message)).scalars().one().summary
        assert "抓取失败 4 条" in s
        assert s.count("、") == 0  # 只剩一个去重后的名字
        assert "等" in s

    def test_summary_fits_the_column(self, db):
        """summary 列宽 300：源再多、名字再长也不该被数据库截断或报错。"""
        _admin(db)
        for i in range(50):
            _source(db, f"s{i}", status="error", name="超长信源名称" * 10)
        assert alert_mod.notify_unhealthy_sources(db) == 1
        assert len(db.execute(select(Message)).scalars().one().summary) <= 300

    def test_aggregated_advances_anchor_for_every_source(self, db):
        """被汇总带上的源都要推进锚点，否则下一轮整批重发。"""
        _admin(db)
        for k in "abcd":
            _source(db, k, status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        db.expire_all()
        assert all(db.get(RssSource, k).last_alerted_at for k in "abcd")
        assert alert_mod.notify_unhealthy_sources(db) == 0

    def test_threshold_is_configurable(self, monkeypatch, db):
        monkeypatch.setattr(alert_mod, "FEED_ALERT_AGGREGATE_MIN", 2)
        _admin(db)
        _source(db, "a", status="error")
        _source(db, "b", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1
        assert db.execute(select(Message)).scalars().one().title == "信源异常汇总（2 条）"

    def test_throttled_sources_stay_out_of_the_batch(self, db):
        """节流窗口内的源不进汇总：数量随之下降，甚至退回逐源。"""
        _admin(db)
        _source(db, "old1", status="error", alerted_minutes_ago=10)
        _source(db, "old2", status="error", alerted_minutes_ago=10)
        _source(db, "fresh", status="error")
        assert alert_mod.notify_unhealthy_sources(db) == 1  # 只有 fresh 到期
        assert db.execute(select(Message)).scalars().one().title == "信源异常提醒"

    def test_ordering_is_deterministic_by_weight(self, db):
        """权重高的源排前面——汇总里被列出的名字就是"最该先看的几个"。"""
        _admin(db)
        _source(db, "low", status="error", weight=1)
        _source(db, "high", status="error", weight=9)
        assert [i["key"] for i in alert_mod.unhealthy_sources(db)] == ["high", "low"]


class TestFailureGrading:
    """P1-18：失败分级 → 连续失败计数 → 熔断 → （可选）自动禁用。

    此前所有失败一视同仁（404 也退避重试两遍）：既浪费请求、烦扰对方站点，
    也说不清"该继续等还是该换源"——`last_status=error` 里看不出失败的**性质**。
    """

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        """跳过退避等待：真实 sleep 会让每个可重试失败白等约 1.8s。"""
        monkeypatch.setattr(ingest_mod.time, "sleep", lambda *_: None)

    # ---- 分类 ----

    def test_5xx_and_429_are_transient(self):
        """服务端的错与限流都是"稍后再来"，退避重试正是对的做法。"""
        for code in (429, 500, 502, 503, 504):
            assert fetcher.classify_status(code) == fetcher.TRANSIENT

    def test_4xx_is_permanent(self):
        """401/403 重试不会突然有权限，404/410 地址不会自己回来。"""
        for code in (400, 401, 403, 404, 410):
            assert fetcher.classify_status(code) == fetcher.PERMANENT

    def test_network_error_is_transient(self):
        class Boom:
            def get(self, url, headers=None):
                raise ConnectionError("连接被重置")

        with pytest.raises(fetcher.FetchError) as ei:
            fetcher.fetch_source(Boom(), _src("x", "https://x.com/f.xml"), 10)
        assert ei.value.kind == fetcher.TRANSIENT

    def test_no_items_is_permanent(self):
        """200 却解析不出条目：HTTP 层是通的，立刻再取一次几乎必然是同一份内容。"""
        empty = ScriptedClient({"f.xml": Resp(200, "<rss version='2.0'><channel/></rss>")})
        with pytest.raises(fetcher.FetchError) as ei:
            fetcher.fetch_source(empty, _src("x", "https://x.com/f.xml"), 10)
        assert ei.value.kind == fetcher.PERMANENT

    # ---- 重试只对可重试失败 ----

    def test_permanent_failure_is_not_retried(self):
        """404 重试再多次也是 404：不该白白多打 N 个请求。"""
        client = ScriptedClient({"f.xml": Resp(404, "")})
        out = _fetch_one(client, _src("p", "https://p.com/f.xml"), 10, 0)
        assert out.error == "HTTP 404"
        assert out.kind == fetcher.PERMANENT
        assert len(client.calls) == 1  # 旧行为是 3 次

    def test_transient_failure_is_retried(self):
        client = ScriptedClient({"f.xml": Resp(503, "")})
        out = _fetch_one(client, _src("t", "https://t.com/f.xml"), 10, 0)
        assert out.kind == fetcher.TRANSIENT
        assert len(client.calls) == 3  # 首试 + FEED_RETRY_TRANSIENT(2) 次退避重试

    def test_429_is_retried(self):
        """限流是"稍后再来"的显式信号，必须重试而不是当成永久失败。"""
        client = ScriptedClient({"f.xml": Resp(429, "")})
        out = _fetch_one(client, _src("r", "https://r.com/f.xml"), 10, 0)
        assert out.kind == fetcher.TRANSIENT
        assert len(client.calls) == 3

    # ---- 连续失败计数（落库）----

    def test_consecutive_failures_are_counted_in_db(self, db):
        """计数必须**落库**：进程内计数在多实例下各算各的，重启也会丢。"""
        _source(db, "flaky")
        for _ in range(3):
            src_mod.record_fetch_result(db, "flaky", False, 0, "超时", kind=fetcher.TRANSIENT)
        row = db.get(RssSource, "flaky")
        assert row.consecutive_failures == 3
        assert row.failure_kind == fetcher.TRANSIENT

    def test_success_resets_counter_and_clears_cooldown(self, db):
        """成功即**彻底**解除熔断，不必等冷却自然到期。"""
        _source(db, "heals")
        for _ in range(6):
            src_mod.record_fetch_result(db, "heals", False, 0, "超时", kind=fetcher.TRANSIENT)
        assert db.get(RssSource, "heals").next_retry_at  # 已熔断
        src_mod.record_fetch_result(db, "heals", True, 5, None)
        row = db.get(RssSource, "heals")
        assert row.consecutive_failures == 0
        assert row.failure_kind is None
        assert row.next_retry_at is None
        assert row.last_status == "ok"

    # ---- 熔断 ----

    def test_circuit_opens_only_at_threshold(self, monkeypatch, db):
        monkeypatch.setattr(src_mod, "FEED_CIRCUIT_THRESHOLD", 3)
        _source(db, "dying")
        for _ in range(2):
            src_mod.record_fetch_result(db, "dying", False, 0, "超时", kind=fetcher.TRANSIENT)
        assert db.get(RssSource, "dying").next_retry_at is None  # 未达阈值
        src_mod.record_fetch_result(db, "dying", False, 0, "超时", kind=fetcher.TRANSIENT)
        assert db.get(RssSource, "dying").next_retry_at  # 达阈值才熔断

    def test_circuit_skips_without_any_request(self):
        """熔断期内一个请求都不发：既省自己的时间，也不去烦正在故障的站点。"""
        src = _src(
            "cooling", "https://cooling.com/f.xml",
            consecutive_failures=5,
            next_retry_at=(datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds"),
        )
        client = ScriptedClient({"f.xml": Resp(200, RSS_ONE.format(title="t", link="l"))})
        out = _fetch_one(client, src, 10, 0)
        assert out.skipped is True
        assert out.skip_reason == SKIP_CIRCUIT
        assert client.calls == []

    def test_circuit_clears_after_cooldown_and_source_heals(self):
        """冷却到期后自动恢复尝试（自愈），无需人工干预。"""
        src = _src(
            "thawed", "https://thawed.com/f.xml",
            consecutive_failures=5,
            next_retry_at=(datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        assert _circuit_open(src) is False
        client = ScriptedClient({"f.xml": Resp(200, RSS_ONE.format(title="t", link="l"))})
        out = _fetch_one(client, src, 10, 0)
        assert out.skipped is False
        assert len(out.items) == 1

    # ---- 自动禁用（默认关闭）----

    def test_auto_disable_is_off_by_default(self, db):
        """默认关闭：源临时 404 一周就被静默关掉、且没人记得打开，比一直重试更糟。"""
        assert src_mod.FEED_AUTO_DISABLE_PERMANENT == 0
        _source(db, "dead")
        for _ in range(10):
            src_mod.record_fetch_result(db, "dead", False, 0, "HTTP 404", kind=fetcher.PERMANENT)
        assert db.get(RssSource, "dead").enabled is True

    def test_auto_disable_ignores_transient_failures(self, monkeypatch, db):
        """可重试失败永远不该触发禁用——网络抖一下不代表源没用了。"""
        monkeypatch.setattr(src_mod, "FEED_AUTO_DISABLE_PERMANENT", 2)
        _source(db, "flaky")
        for _ in range(5):
            src_mod.record_fetch_result(db, "flaky", False, 0, "超时", kind=fetcher.TRANSIENT)
        row = db.get(RssSource, "flaky")
        assert row.enabled is True
        assert row.consecutive_failures == 5

    def test_auto_disable_writes_audit_trace(self, monkeypatch, db):
        """开启后触发**必须留痕**：「谁把源关了」事后查不到，是最难排查的一类问题。"""
        monkeypatch.setattr(src_mod, "FEED_AUTO_DISABLE_PERMANENT", 2)
        _source(db, "gone")
        for _ in range(2):
            src_mod.record_fetch_result(db, "gone", False, 0, "HTTP 404", kind=fetcher.PERMANENT)
        row = db.get(RssSource, "gone")
        assert row.enabled is False
        log = db.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "source.auto_disable")
        ).scalars().one()
        assert log.target == "gone"
        assert log.actor_phone == "system"  # 无人类操作者，但要可追溯
        assert "consecutive_failures" in log.detail

    # ---- 端到端 ----

    def test_kind_survives_the_thread_pool(self):
        """异常跨线程会被 `str(e)` 抹平，kind 必须**结构化**带回主线程。"""
        stats: list[dict] = []
        collect(
            sources=[_src("dead", "https://dead.com/f.xml")],
            limit_per_source=10,
            stats=stats,
            client=ScriptedClient({"f.xml": Resp(404, "")}),
        )
        assert stats[0]["ok"] is False
        assert stats[0]["kind"] == fetcher.PERMANENT

    # ---- 告警里的失败性质 ----

    def test_alert_reason_carries_streak_and_nature(self, db):
        """告警要能回答"等一等，还是得去改"——这是 P1-18 对运营最直接的价值。"""
        _source(db, "p", status="error", fail_kind=fetcher.PERMANENT, failures=3)
        _source(db, "t", status="error", fail_kind=fetcher.TRANSIENT, failures=1)
        reasons = {i["key"]: i["reason"] for i in alert_mod.unhealthy_sources(db)}
        assert "连续 3 次失败" in reasons["p"]
        assert "永久性，需人工处理" in reasons["p"]
        assert "可重试" in reasons["t"]
        assert "连续" not in reasons["t"]  # 只失败一次不该说"连续"

    def test_batch_summary_separates_permanent_from_transient(self, db):
        """汇总按**失败性质**再分组：全临时 = 抖动，全永久 = 真的坏了。"""
        _admin(db)
        for k in "ab":
            _source(db, k, status="error", fail_kind=fetcher.TRANSIENT)
        for k in "cd":
            _source(db, k, status="error", fail_kind=fetcher.PERMANENT)
        alert_mod.notify_unhealthy_sources(db)
        s = db.execute(select(Message)).scalars().one().summary
        assert "永久失败 2 条" in s
        assert "临时失败 2 条" in s
        assert s.index("永久失败") < s.index("临时失败")  # 需人工的排前面

    def test_legacy_errors_without_nature_fall_back(self, db):
        """P1-18 之前落的错误没有性质，兜底成「抓取失败」而不是消失。"""
        _admin(db)
        for k in "abc":
            _source(db, k, status="error")
        alert_mod.notify_unhealthy_sources(db)
        s = db.execute(select(Message)).scalars().one().summary
        assert "抓取失败 3 条" in s
