"""P1-76（E7-a）：管理端运营大盘（`GET /api/admin/dashboard`）。

设计见 `docs/后端/E7运营数据看板设计.md`。覆盖五条线：

1. **板块齐全**——单端点返回全部 5 个板块（内容 / 待办 / 互动 / 源健康 / 审核命中），
   且 `days` 回显；
2. **口径护栏**——`news` 与 `newsHidden` 分开数（只数可见数等于"下架了多少"
   永远看不出来）；`todo.reports` 只数 `pending`（自动软隐藏也只算 pending）；
   下架量按 `hidden_at` 过滤而不是"当前是否隐藏"（恢复过又隐藏的不能漏）；
3. **`days` 收敛**——0 / 负数 / 超保留期都回到等宽窗口（`window_days`）；
4. **鉴权**——未登录 401、非管理员 403（结构性守卫另见 `test_admin_guard.py`，
   它用 OpenAPI 枚举全部 admin 操作，本端点自动受覆盖）；
5. **PG 护栏**（本批重点）——按天分桶走 SQL 侧 `substr(created_at, 1, 10)`，
   而这些列是 `String(30)`；D1-2 打算把 `hit_logs.*` 迁成 `DateTime`，
   届时该查询会在 PG 上报 `substr(timestamptz, …) does not exist`
   （P1-74 同款事故：SQLite 单测全绿、只有真实 PG 会炸）。
   本条直连 PG 跑一遍，让迁移先红在测试上。

关于 `similarity()` / pg_trgm：本模块不涉及模糊匹配，无需垫片。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"
os.environ["CACHE_ENABLED"] = "false"

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.metrics import WORST_SOURCES_LIMIT, build_dashboard  # noqa: E402
from app.models import (  # noqa: E402
    Comment,
    NicknameHit,
    News,
    RssSource,
    RssSourceHealthEvent,
    SearchLog,
    SensitiveHit,
    User,
    now_str,
)

ADMIN_PHONE = "13800138000"  # 根 conftest 固定的号段

# 整点锚点：跨午夜的用例都从"当天的固定时刻"取，避免"测试跑在 00:30
# 把事件甩到相邻日期上"（凌晨跑就红）
BASE = (datetime.now(timezone.utc) - timedelta(days=5)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def _admin(client) -> dict:
    client.post(
        "/api/auth/register",
        json={"phone": ADMIN_PHONE, "password": "abc123", "name": "管理员"},
    )
    r = client.post("/api/auth/login", json={"phone": ADMIN_PHONE, "password": "abc123"})
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _stamp(days_ago: int = 0, hour: int = 9) -> str:
    """`days_ago` 天前当天的 `hour` 点（UTC aware ISO，与 `now_str()` 同格式）。

    锚在"当天的固定时刻"而不是"往前推 N 小时"：后者在跨午夜时（例如测试跑在
    00:30）会把事件甩到相邻日期上，同一天的两条记录被算成两天，凌晨跑就红。
    """
    day = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
    return datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=timezone.utc).isoformat(
        timespec="seconds"
    )


def _news(nid: str, *, hidden: bool = False, hidden_at: str | None = None) -> None:
    with SessionLocal() as db:
        db.add(
            News(
                id=nid,
                title=f"资讯{nid}",
                summary="摘要",
                source="测试源",
                comment_count=0,
                time="1小时前",
                channel="finance",
                variant="text",
                published_at=_stamp(1),
                hidden=hidden,
                hidden_at=hidden_at,
            )
        )
        db.commit()


def _source(key: str, name: str) -> None:
    with SessionLocal() as db:
        db.add(
            RssSource(
                key=key,
                name=name,
                channel="finance",
                url=f"https://{key}.example/f.xml",
                weight=5,
                enabled=True,
                created_at=now_str(),
                updated_at=now_str(),
            )
        )
        db.commit()


def _health(key: str, ok: bool, items: int, at: str) -> None:
    with SessionLocal() as db:
        db.add(
            RssSourceHealthEvent(source_key=key, ok=ok, item_count=items, at=datetime.fromisoformat(at))
        )
        db.commit()


def _hit(word: str, created_at: str) -> None:
    with SessionLocal() as db:
        db.add(
            SensitiveHit(
                word=word,
                action="mask",
                field="content",
                user_name="甲",
                snippet="片段",
                created_at=created_at,
            )
        )
        db.commit()


def _nickname_hit(word: str, created_at: str) -> None:
    with SessionLocal() as db:
        db.add(NicknameHit(word=word, action="block", user_name="乙", created_at=created_at))
        db.commit()


# ----------------------------- 端点契约 -----------------------------


class TestDashboardEndpoint:
    def test_returns_all_blocks(self, client):
        h = _admin(client)
        r = client.get("/api/admin/dashboard", headers=h)

        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        d = body["data"]
        # 五个板块一个都不能少：少一个就是"页面有一块是空的"，而接口层看不出来
        assert set(d) == {
            "days",
            "generatedAt",
            "content",
            "todo",
            "engagement",
            "sourceHealth",
            "moderation",
            "search",
        }
        assert d["days"] == 30  # 未传 days 时收敛到保留期
        assert d["generatedAt"].endswith("+00:00")  # 双端格式一致（P1-19 规矩）

    def test_days_echoed_and_converged(self, client):
        h = _admin(client)
        assert client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]["days"] == 7
        # 超出保留期（30 天）的请求被收敛到等宽窗口：与其让前端渲染 90 个空格子，
        # 不如给一个与数据等宽的窗口（与单源趋势端点同处方）
        assert (
            client.get("/api/admin/dashboard?days=90", headers=h).json()["data"]["days"] == 30
        )

    def test_days_out_of_query_range_rejected(self, client):
        """0 / 负数 / 超上限在**校验层**就被拒（`Query(ge=1, le=365)`）——
        与 `/api/admin/sources/{key}/health` 同一约束，收敛只负责 31~365 这一段。"""
        h = _admin(client)
        for bad in ("0", "-1", "999", "abc"):
            r = client.get(f"/api/admin/dashboard?days={bad}", headers=h)
            assert r.status_code == 422, bad
            assert r.json()["code"] == 42200, bad

    def test_requires_admin(self, client):
        # 未登录 → 401（不能被 404/422 抢先）
        assert client.get("/api/admin/dashboard").status_code == 401
        # 已登录非管理员 → 403
        client.post(
            "/api/auth/register",
            json={"phone": "13700000001", "password": "abc123", "name": "路人"},
        )
        r = client.post("/api/auth/login", json={"phone": "13700000001", "password": "abc123"})
        h = {"Authorization": f"Bearer {r.json()['data']['token']}"}
        assert client.get("/api/admin/dashboard", headers=h).status_code == 403

    def test_empty_db_does_not_explode(self, client):
        """新环境第一眼看到的大盘就该是 0 / 空列表，而不是 500。"""
        h = _admin(client)
        d = client.get("/api/admin/dashboard", headers=h).json()["data"]

        assert d["content"] == {
            "news": 0,
            "newsHidden": 0,
            "comments": 0,
            "users": 1,  # 管理员自己
            "sources": 0,
            "sourcesEnabled": 0,
        }
        assert d["todo"] == {"reports": 0, "commentReviews": 0, "nicknameReviews": 0}
        assert d["sourceHealth"]["worst"] == []
        assert d["sourceHealth"]["unhealthy"] == 0
        assert d["moderation"]["hitsTrend"] == []
        assert d["moderation"]["hits"] == {"sensitive": 0, "nickname": 0}


# ----------------------------- 口径护栏 -----------------------------


class TestDashboardSemantics:
    def test_visible_and_hidden_counted_separately(self, client):
        """只数可见数的话，"下架了多少"永远看不出来。"""
        h = _admin(client)
        _news("v1")
        _news("v2")
        _news("h1", hidden=True, hidden_at=_stamp(0))

        d = client.get("/api/admin/dashboard", headers=h).json()["data"]
        assert d["content"]["news"] == 3
        assert d["content"]["newsHidden"] == 1

    def test_moderation_counts_by_hidden_at_not_current_flag(self, client):
        """按 `hidden_at` 过滤：恢复过又隐藏的不能漏，历史上架过的不算进来。"""
        h = _admin(client)
        # 窗口内下架（应计入）
        _news("m1", hidden=True, hidden_at=_stamp(0))
        # 更早下架（超出 7 天窗口，不计入）
        _news("m2", hidden=True, hidden_at=_stamp(40))
        # 窗口内下架又恢复（hidden_at 已置 NULL，不计入）
        _news("m3", hidden=True, hidden_at=None)
        # 当前可见且从未下架（不计入）
        _news("m4")

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        assert d["moderation"]["newsHidden"] == 1
        assert d["moderation"]["commentsHidden"] == 0

    def test_todo_reports_counts_only_pending(self, client):
        """已处置/驳回不该继续占着待办数（自动软隐藏也只算 pending）。"""
        h = _admin(client)
        with SessionLocal() as db:
            u = User(phone="13900000001", name="举报者", password_hash="x")
            db.add(u)
            db.commit()
            uid = u.id
            for i, status in enumerate(("pending", "resolved", "dismissed", "pending")):
                db.add(
                    __import__("app.models", fromlist=["Report"]).Report(
                        reporter_id=uid,
                        target_type="news",
                        target_id=f"t{i}",
                        reason="spam",
                        status=status,
                        created_at=now_str(),
                    )
                )
            db.commit()

        d = client.get("/api/admin/dashboard", headers=h).json()["data"]
        assert d["todo"]["reports"] == 2

    def test_worst_sources_sorted_by_failure_rate(self, client):
        """顺序是断言点：按失败率倒序（最差的在前），不是成员包含。"""
        h = _admin(client)
        for key, name in (("s_ok", "全成功源"), ("s_bad", "很惨源"), ("s_mid", "半残源")):
            _source(key, name)
        # s_bad：5 次里 4 次失败 → rate 0.2。失败轮次的条数按真实情况记 0
        # （avgItems 的分母是**成功轮次**，失败轮混进真实条数会把均值抬起来）
        for i in range(5):
            _health("s_bad", ok=(i == 0), items=(3 if i == 0 else 0), at=_stamp(0, hour=9 + i))
        # s_mid：4 次里 2 次失败 → rate 0.5
        for i in range(4):
            _health("s_mid", ok=(i < 2), items=(5 if i < 2 else 0), at=_stamp(0, hour=9 + i))
        # s_ok：3 次全成功 → 不是"不健康"
        for i in range(3):
            _health("s_ok", ok=True, items=10, at=_stamp(0, hour=9 + i))
        # 空闲源（窗口内无尝试）不该被算进 unhealthy
        _source("s_idle", "空闲源")

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        health = d["sourceHealth"]
        assert health["windowDays"] == 7
        assert health["unhealthy"] == 2  # 只有真有失败的
        assert [w["key"] for w in health["worst"]] == ["s_bad", "s_mid"]
        assert health["worst"][0]["failed"] == 4 and health["worst"][0]["attempts"] == 5
        assert health["worst"][0]["rate"] == 0.2
        assert health["worst"][0]["avgItems"] == 3.0
        assert health["worst"][0]["lastFailAt"] is not None
        # 全成功的源带 name 快照（运营认的是名字，不是 key）
        assert health["worst"][0]["name"] == "很惨源"

    def test_worst_sources_capped(self, client):
        """概览不是报表：最差源只给 Top N。"""
        h = _admin(client)
        for i in range(WORST_SOURCES_LIMIT + 3):
            key = f"s{i}"
            _source(key, f"源{i}")
            _health(key, ok=False, items=0, at=_stamp(0, hour=9))

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        assert len(d["sourceHealth"]["worst"]) == WORST_SOURCES_LIMIT
        assert d["sourceHealth"]["unhealthy"] == WORST_SOURCES_LIMIT + 3

    def test_hits_trend_buckets_by_utc_day(self, client):
        """按天分桶、升序、两序列齐全；同一天只有一个点。"""
        h = _admin(client)
        _hit("词A", _stamp(2, hour=9))
        _hit("词B", _stamp(2, hour=15))
        _hit("词C", _stamp(0, hour=9))
        _nickname_hit("昵", _stamp(0, hour=10))

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        trend = d["moderation"]["hitsTrend"]
        assert [p["date"] for p in trend] == [_stamp(2)[:10], _stamp(0)[:10]]
        assert trend[0] == {"date": _stamp(2)[:10], "sensitive": 2, "nickname": 0}
        assert trend[1] == {"date": _stamp(0)[:10], "sensitive": 1, "nickname": 1}
        assert d["moderation"]["hits"] == {"sensitive": 3, "nickname": 1}

    def test_hits_outside_window_excluded(self, client):
        """窗口外的命中不出现在趋势里（否则"最近正常吗"答不了）。"""
        h = _admin(client)
        _hit("老词", _stamp(40))
        _hit("新词", _stamp(0))

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        assert d["moderation"]["hits"] == {"sensitive": 1, "nickname": 0}
        assert [p["date"] for p in d["moderation"]["hitsTrend"]] == [_stamp(0)[:10]]

    def test_engagement_counts_use_window(self, client):
        """互动量是窗口内的，不是存量。"""
        h = _admin(client)
        with SessionLocal() as db:
            db.add(SearchLog(id="sl_in", term="窗内词", created_at=_stamp(0)))
            db.add(SearchLog(id="sl_out", term="窗外词", created_at=_stamp(40)))
            db.commit()

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        assert d["engagement"]["searches"] == 1

    def test_hot_terms_reuse_search_page_caliber(self, client):
        """运营看到的与用户看到的应当是同一批词。"""
        h = _admin(client)
        with SessionLocal() as db:
            db.add(SearchLog(id="sl_hot", term="热搜词甲", created_at=_stamp(0)))
            db.commit()

        d = client.get("/api/admin/dashboard?days=7", headers=h).json()["data"]
        assert [t["term"] for t in d["search"]["hot"]][:1] == ["热搜词甲"]

    def test_generated_at_is_injected_now(self, client):
        """纯函数口径：`now` 注入后 `generatedAt` 与窗口截止都跟着走（可回放）。"""
        fixed = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        with SessionLocal() as db:
            d = build_dashboard(db, 7, now=fixed)
        assert d["generatedAt"] == "2026-10-20T12:00:00+00:00"


# ----------------------------- PG 护栏（D4 / D1-2） -----------------------------


class TestDashboardOnPostgres:
    """按天分桶的 PG 护栏。

    这些列（`sensitive_hits.created_at` / `nickname_hits.created_at`）现在是
    `String(30)`，`substr(col, 1, 10)` 在 PG 与 SQLite 上行为一致。
    **但 D1-2 明确把 `hit_logs.*` 列入了"按需再迁 DateTime"的清单**——一旦迁移，
    这条查询会在 PG 上报 `function substr(timestamp with time zone, integer, integer)
    does not exist`，而 SQLite 单测全绿（P1-74 同款事故）。

    本条直连 PG 跑一遍（`TEST_PG_URL`，CI 的 postgres service 已在同一 job 内）。
    连不上就跳过；连上了就必须过——这是唯一能在单元测试里复现该缺陷的方式。
    """

    PROBE_WORD = "__pytest_dash_probe__"

    @pytest.fixture
    def pg(self):
        url = os.environ.get("TEST_PG_URL", "")
        if not url:
            pytest.skip("未配置 TEST_PG_URL")
        eng = None
        conn = None
        try:
            eng = create_engine(url)
            conn = eng.connect()
            conn.execute(text("SELECT 1"))
            conn.rollback()  # 结束 autobegin，下面才能显式 begin()
        except Exception as e:  # noqa: BLE001 - 连不上就跳过，不是失败
            if conn is not None:
                conn.close()
            if eng is not None:
                eng.dispose()
            pytest.skip(f"连不上 PostgreSQL：{type(e).__name__}")
        # 整段包在事务里、用完回滚：不在真实库里留探针行，
        # 也不需要库已 migrate（create_all 在事务内，PG 的事务性 DDL 会一并回滚）
        trans = conn.begin()
        try:
            Base.metadata.create_all(bind=conn)
            yield conn
        finally:
            trans.rollback()
            conn.close()
            eng.dispose()

    def test_trend_query_runs_on_datetime_capable_dialect(self, pg):
        """造两天的命中，跑一遍聚合：SQL 侧 substr 必须在 PG 上真的能执行。

        先清掉两张表里**已有的行**：直连的是开发库，里面有历史命中数据，
        不清的话趋势里会混进无关日期，断言就不再说明它想说明的事。
        删除包在事务里，fixture 收尾整体回滚，不会真的动到数据。
        """
        day_a = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        day_b = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with Session(bind=pg) as db:
            db.execute(SensitiveHit.__table__.delete())
            db.execute(NicknameHit.__table__.delete())
            for word, day in ((self.PROBE_WORD, day_a), (self.PROBE_WORD, day_b)):
                db.add(
                    SensitiveHit(
                        word=word,
                        action="mask",
                        field="content",
                        user_name="探针",
                        snippet="片段",
                        created_at=f"{day}T09:00:00+00:00",
                    )
                )
                db.add(
                    NicknameHit(
                        word=word,
                        action="block",
                        user_name="探针",
                        created_at=f"{day}T10:00:00+00:00",
                    )
                )
            db.flush()  # 不用 commit：外层事务由 fixture 回滚
            d = build_dashboard(db, 7)

        assert [p["date"] for p in d["moderation"]["hitsTrend"]] == [day_a, day_b]
        assert d["moderation"]["hits"] == {"sensitive": 2, "nickname": 2}
