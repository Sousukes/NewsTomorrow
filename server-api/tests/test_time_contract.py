"""时间口径的两道护栏（P1-50：为 D1 改列类型准备安全网）。

第一道是**用户可见的 bug**：`comments.time` / `messages.time` 同时装着 ISO
时间串与「1小时前」展示文案，`to_out` 原样透传 → 用户新发的评论显示
`2026-10-18T14:42:17+00:00`。修法不是"换一种存法"（那要迁数据），而是
**展示文案由绝对时间推导**。

第二道是**格式契约**：backlog 的 D1 要把这批 `String(30)` 改成
`TIMESTAMP`。能不能安全改，取决于「同格式 ISO 的字典序 = 时间序」这条契约
今天是否还成立。这里把它钉成断言——**契约破了要当场红，而不是等迁移后
排序静默出错**。

运行：
    cd server-api
    pytest tests/test_time_contract.py -q
"""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.models import (  # noqa: E402
    Comment,
    News,
    display_time,
    iso_utc,
    now_dt,
    now_str,
    parse_dt,
)

NOW = datetime(2026, 10, 18, 14, 0, 0, tzinfo=timezone.utc)


class TestDisplayTime:
    def test_iso_renders_relative_text(self):
        """用户新建的评论存的是 ISO 串，必须渲染成相对文案而不是原样透传。"""
        iso = (NOW - timedelta(minutes=5)).isoformat(timespec="seconds")
        assert display_time(iso, iso, now=NOW) == "5 分钟前"

    def test_seconds_are_just_now(self):
        iso = (NOW - timedelta(seconds=30)).isoformat(timespec="seconds")
        assert display_time(iso, iso, now=NOW) == "刚刚"

    def test_hours_and_days(self):
        h = (NOW - timedelta(hours=3)).isoformat(timespec="seconds")
        assert display_time(h, h, now=NOW) == "3 小时前"
        d = (NOW - timedelta(days=2)).isoformat(timespec="seconds")
        assert display_time(d, d, now=NOW) == "2 天前"

    def test_long_ago_falls_back_to_date(self):
        old = (NOW - timedelta(days=30)).isoformat(timespec="seconds")
        out = display_time(old, old, now=NOW)
        assert "2026-09" in out  # 不再是相对文案，给绝对日期

    def test_existing_display_text_is_preserved(self):
        """演示种子的「1小时前」不是时间（那是排版效果），不能被覆盖成别的。"""
        assert display_time("1小时前", None, now=NOW) == "1小时前"

    def test_falls_back_to_absolute_when_stored_empty(self):
        iso = (NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        assert display_time("", iso, now=NOW) == "1 小时前"

    def test_future_time_does_not_render_negative(self):
        """时钟漂移/时区错配可能给出未来时间；不能显示「-3 分钟前」。"""
        future = (NOW + timedelta(minutes=3)).isoformat(timespec="seconds")
        out = display_time(future, future, now=NOW)
        # 绝不能出现「-3 分钟前」这种负数文案
        assert not out.startswith("-")
        assert "前" not in out
        assert "2026-10-18" in out

    def test_both_empty_returns_empty(self):
        assert display_time("", "", now=NOW) == ""


class TestWriteFormatContract:
    """D1 的前置条件：写库时间串必须同格式，字典序 == 时间序。"""

    def test_now_str_is_utc_with_offset(self):
        s = now_str()
        assert s.endswith("+00:00"), f"缺少时区偏移：{s}"
        dt = parse_dt(s)
        assert dt is not None and dt.tzinfo is not None

    def test_now_str_second_precision(self):
        """秒精度：微秒会让 `String(30)` 截断，也会让同秒行的比较变得随机。"""
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", now_str())

    def test_lexicographic_order_matches_chronological(self):
        """D1 之所以能靠迁移回填保序，全靠这条。破了她，红在这里。"""
        base = datetime(2026, 10, 18, 14, 0, 0, tzinfo=timezone.utc)
        moments = [
            base - timedelta(days=1),
            base - timedelta(seconds=1),
            base,
            base + timedelta(seconds=1),
        ]
        samples = [m.isoformat(timespec="seconds") for m in moments]
        assert samples == sorted(samples), f"字典序与时间序不一致：{samples}"

    def test_models_have_no_naive_datetime_defaults(self):
        """`datetime.now()`（naive 本地时间）会重新引入 A3 清掉的偏差。

        **用 AST 而不是文本搜索**：`models.py` 的文档字符串里多次以"不要再引入
        datetime.now()"的形式提到它，按子串匹配会把这些说明当成违规——
        护栏自己被文档绊倒，就再也不会有人看它了。
        """
        import ast

        tree = ast.parse(open("app/models.py", encoding="utf-8").read())
        offenders: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "now"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
                and not node.args  # datetime.now(tz) 传了参数才算合规
            ):
                offenders.append(node.lineno)
        assert not offenders, f"models.py 仍调用 naive datetime.now()，行号：{offenders}"


class TestUserVisibleFix:
    @pytest.fixture
    def client(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with TestClient(main.app) as c:
            yield c

    def test_new_comment_shows_relative_text_not_raw_iso(self, client):
        """端到端：发一条评论 → 列表里的 time 必须是可读文案。"""
        from app.database import SessionLocal

        ts = now_str()
        with SessionLocal() as db:
            db.add(
                News(
                    id=9001,
                    title="用于时间展示测试",
                    summary="",
                    source="t",
                    published_at=ts,
                )
            )
            db.commit()
            news_id = 9001

        with SessionLocal() as db:
            db.add(
                Comment(
                    id=9101,
                    news_id=news_id,
                    user_name="甲",
                    seed="me",
                    content="时间契约测试",
                    time=ts,
                    created_at=ts,
                )
            )
            db.commit()

        r = client.get(f"/api/news/{news_id}/comments")
        assert r.status_code == 200
        row = next(c for c in r.json()["data"]["list"] if c["id"] == "9101")
        # 核心断言：用户可见的是相对文案，不是原始 ISO 串
        assert not row["time"].startswith("20")
        assert row["time"] in {"刚刚", "1 分钟前", "2 分钟前"} or "分钟前" in row["time"]

    def test_created_at_stays_absolute_iso(self, client):
        """展示层改文案，但排序/过滤依赖的 createdAt 必须仍是绝对 ISO。"""
        from app.database import SessionLocal

        ts = now_str()
        with SessionLocal() as db:
            # 列表端点按"资讯存在"才返回：只造评论会得到 data=null 的 404
            db.add(News(id=9200, title="时间契约测试", summary="", source="t", published_at=ts))
            db.add(
                Comment(
                    id=9201,
                    news_id=9200,
                    user_name="甲",
                    seed="me",
                    content="时间契约测试",
                    time=ts,
                    created_at=ts,
                )
            )
            db.commit()

        row = next(
            c
            for c in client.get("/api/news/9200/comments").json()["data"]["list"]
            if c["id"] == "9201"
        )
        # 展示层是相对文案，但排序/过滤依赖的 createdAt 仍是绝对 ISO
        assert "前" in row["time"] or row["time"] == "刚刚"
        assert row["createdAt"] == ts
        assert row["createdAt"].endswith("+00:00")


class TestDateTimeColumnBoundary:
    """D1-1（`rss_source_health_events.at` 改 `DateTime`）的边界约定。

    核心风险是**双端格式不一致**：PG 的 timestamptz 读回带时区，
    SQLite 的 DateTime 读回**不带**——同一个值出响应时前者是
    `...+00:00`、后者是裸 `...`，前端 `new Date()` 的解释会随部署环境变化。
    所以出响应的最后一律过 `iso_utc()`，这里把这件事钉住。
    """

    @pytest.fixture(autouse=True)
    def _schema(self):
        """本类需要真实表（fixture 是类内作用域，不能借用别的类的）。"""
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        yield
        Base.metadata.drop_all(bind=engine)

    def test_iso_utc_always_carries_offset(self):
        from datetime import timezone as _tz

        naive = datetime(2026, 10, 18, 14, 0, 0)          # SQLite 读回就是这样
        aware = datetime(2026, 10, 18, 14, 0, 0, tzinfo=_tz.utc)  # PG 读回
        assert iso_utc(naive).endswith("+00:00")
        assert iso_utc(aware).endswith("+00:00")
        assert iso_utc(naive) == iso_utc(aware), "两端同一时刻应给出同一个串"

    def test_iso_utc_passes_none_through(self):
        assert iso_utc(None) is None

    def test_non_utc_datetime_is_converted_not_dropped(self):
        """带 +08:00 的值必须转成 UTC，而不是原样带着 +08:00 出响应——
        那会让同一批数据在不同部署下显示不同的本地时间。"""
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        # tzinfo 必须是 timezone 实例，不能直接塞 timedelta
        shanghai = datetime(2026, 10, 18, 22, 0, 0, tzinfo=_tz(_td(hours=8)))
        out = iso_utc(shanghai)
        assert out == "2026-10-18T14:00:00+00:00"

    def test_health_summary_time_fields_always_have_offset(self):
        """走真实数据路径（`summarize` 直接读库），出参必须带偏移。

        不用管理端点做这层验证：它需要鉴权令牌，而这批要验的是**时间格式**，
        引入 token 只会让失败原因变模糊。
        """
        from app.database import SessionLocal
        from app.feeds import health as health_mod
        from app.models import RssSourceHealthEvent, now_dt

        with SessionLocal() as db:
            db.add(RssSourceHealthEvent(source_key="k", ok=True, item_count=1, at=now_dt()))
            db.commit()
            summary = health_mod.summarize(db, "k", 7)
        assert summary["lastOkAt"].endswith("+00:00"), summary["lastOkAt"]
        assert summary["lastFailAt"] is None

    def test_window_filter_accepts_datetime_parameter(self):
        """窗口查询必须以 **datetime 参数**比较（cutoff 不再拼字符串）。"""
        import inspect

        from app.feeds import health as health_mod

        assert inspect.isfunction(health_mod.since)
        anchor = health_mod.since(3)
        assert isinstance(anchor, datetime), f"cutoff 应是 datetime，实际 {type(anchor)}"
        assert anchor.tzinfo is not None, "cutoff 必须是 aware（naive 在 PG 里按会话时区解释）"
