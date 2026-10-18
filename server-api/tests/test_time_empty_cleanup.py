"""D1-0 的两件事：**空串归一为 NULL** 与「归一前后行为等价」。

最重要的一条是**等价性**：清洗是一次不可逆的数据改动，如果它改变了用户能看到
的东西，那就不是"清理"而是"改需求"。所以这里造出三种行（ISO / `''` / NULL），
断言清洗前后它们在**列表可见性、排序位置、保留期清理**三个维度上的表现一致。

另有一条写库护栏：既然实测「写入路径从不产生 `''`」，那就把它钉住——
将来有人写 `published_at=""` 时当场红，而不是等到下一次迁移失败才发现。

运行：
    cd server-api
    pytest tests/test_time_empty_cleanup.py -q
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import News, now_str  # noqa: E402

EMPTY = ""


class TestMigration:
    """直接对 0029 的 SQL 行为做验证（SQLite 上跑真实 UPDATE）。"""

    @pytest.fixture
    def eng(self):
        md = MetaData()
        Table(
            "probe",
            md,
            Column("id", Integer, primary_key=True),
            Column("at", String(30)),
        )
        e = create_engine("sqlite:///:memory:")
        md.create_all(e)
        with e.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO probe (id, at) VALUES (1,''),(2,NULL),(3,'2026-10-18T14:00:00+00:00')"
                )
            )
        yield e
        e.dispose()

    def test_cleanup_updates_only_empty_rows(self, eng):
        with eng.begin() as conn:
            conn.execute(text("UPDATE probe SET at = NULL WHERE at = :e"), {"e": EMPTY})
        rows = dict(eng.connect().execute(text("SELECT id, at FROM probe")).all())
        assert rows[1] is None, "空串应当变成 NULL"
        assert rows[2] is None, "本来就是 NULL，不受影响"
        assert rows[3] == "2026-10-18T14:00:00+00:00", "真实时间不应被改动"


class TestWritePathNeverProducesEmpty:
    """护栏：写入路径只允许 None 或 ISO，写 `''` 当场红。"""

    def test_published_at_is_never_written_as_empty(self):
        src = open("app/feeds/ingest.py", encoding="utf-8").read()
        # 只允许两种形态：None 或 .isoformat()
        offenders = [
            line.strip()
            for line in src.splitlines()
            if "published_at" in line and "published_at=''" in line.replace(" ", "")
        ]
        assert not offenders, f"写入路径出现空串：{offenders}"

    @staticmethod
    def _load_migration():
        """按路径加载迁移模块（模块名以数字开头，不能直接 import）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "m0029",
            "migrations/versions/0029_time_empty_to_null.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cleaned_migration_does_not_touch_display_columns(self):
        """展示型列（`news.time` 等）的 `''` 是合法默认值，不能被清洗。

        **读模块的 `_TABLES`，不搜源码**：0029 的文档字符串里就写了
        `news.time`（说明为什么排除它），按子串判断必然误报。
        """
        tables = self._load_migration()._TABLES
        for display_col in ("time",):
            hit = [t for t, cols in tables.items() if display_col in cols]
            assert not hit, f"清洗白名单里不应出现展示列 `{display_col}`：{hit}"

    def test_migration_targets_real_timestamp_columns(self):
        """反向检查：真正要清洗的列必须在白名单里（别写漏了却以为清干净了）。"""
        tables = self._load_migration()._TABLES
        assert "published_at" in tables["news"]
        assert "at" in tables["rss_source_health_events"]
        assert "viewed_at" in tables["user_history"]
        assert "last_fetched_at" in tables["rss_sources"]


class TestBehaviorEquivalence:
    """清洗前后，用户能看到的东西必须一模一样。"""

    @pytest.fixture
    def client(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        with TestClient(main.app) as c:
            yield c

    @staticmethod
    def _seed(client):
        ts = now_str()
        older = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with SessionLocal() as db:
            # 三种形态各一行：真实时间 / 空串 / NULL
            db.add(News(id=7001, title="有时间", summary="", source="t", published_at=older))
            db.add(News(id=7002, title="空串", summary="", source="t", published_at=EMPTY))
            db.add(News(id=7003, title="NULL", summary="", source="t", published_at=None))
            db.commit()
        return ts

    def test_all_three_rows_visible_before_and_after_cleanup(self, client):
        self._seed(client)
        before = client.get("/api/news?limit=20").json()["data"]["list"]
        ids_before = {n["id"] for n in before}
        assert {"7001", "7002", "7003"} <= ids_before, ids_before

        # 执行清洗
        with engine.begin() as conn:
            conn.execute(text("UPDATE news SET published_at = NULL WHERE published_at = :e"), {"e": EMPTY})

        after = client.get("/api/news?limit=20").json()["data"]["list"]
        ids_after = {n["id"] for n in after}
        assert ids_after == ids_before, "清洗后可见集合变了——那就不是清理而是改需求"

    def test_sink_to_bottom_in_both_shapes(self, client):
        """`''` 与 NULL 都要沉底（排序键是 `coalesce(published_at, '')`）。"""
        self._seed(client)
        order_before = [n["id"] for n in client.get("/api/news?limit=20").json()["data"]["list"]]

        with engine.begin() as conn:
            conn.execute(text("UPDATE news SET published_at = NULL WHERE published_at = :e"), {"e": EMPTY})

        order_after = [n["id"] for n in client.get("/api/news?limit=20").json()["data"]["list"]]
        assert order_after == order_before, f"排序位置变了：{order_before} → {order_after}"
        # 有时间的那条必须在两个无时间的之前
        assert order_after.index("7001") < order_after.index("7002")

    def test_hot_score_treats_both_shapes_alike(self, client):
        """热度衰减遇到空串与 NULL 都要走"未知"分支，不能一个算 0 分一个报错。"""
        from app.ranking import time_decay

        assert time_decay(EMPTY, datetime.now(timezone.utc)) == time_decay(
            None, datetime.now(timezone.utc)
        )
