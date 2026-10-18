"""D1 迁移实验（spike）：`String(30)` → `DateTime` 到底会发生什么。

这不是"实现 D1"，而是**动手前先把假设跑一遍**。本地没有 PG 守护进程，
所以只覆盖 SQLite——而 SQLite 恰恰是 ALTER 更难的一端（没有原生时间类型，
改列要整表重建）。

要回答四个问题，每个都会直接影响 D1 的方案：

1. **存量里到底有什么**——迁移面对的不是"36 个干净的时间列"，而是混杂值；
2. `batch_alter_table` 能否成功；
3. **转换后时区偏移还在不在**（丢了偏移 = 时间被按本地时间重新解释）；
4. 转换后**字典序排序**与**范围比较**是否仍等价于时间序。

运行：
    cd server-api
    pytest tests/test_time_migration_spike.py -q -s
"""
import os

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, select, text

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

TABLE = "spike_events"


def _make_table(values: list[tuple[str, object]]) -> None:
    md = MetaData()
    Table(
        TABLE,
        md,
        Column("id", Integer, primary_key=True),
        Column("at", String(30)),
    )
    eng = create_engine("sqlite:///:memory:")
    md.drop_all(eng)
    md.create_all(eng)
    with eng.begin() as conn:
        for row_id, value in values:
            conn.execute(text(f"INSERT INTO {TABLE} (id, at) VALUES (:i, :a)"), {"i": row_id, "a": value})
    return eng  # type: ignore[return-value]


class TestInventory:
    """问题 1：存量列里实际混着哪些东西。"""

    def test_string_column_holds_more_than_timestamps(self):
        eng = _make_table([
            (1, "2026-10-18T14:00:00+00:00"),   # A3 之后的口径
            (2, "2026-10-18T14:00:00"),          # naive（0027 前的存量）
            (3, ""),                              # 空串：published_at 真的会这样
            (4, None),                            # NULL
            (5, "刚刚"),                           # 展示文案误入时间列
        ])
        rows = list(eng.connect().execute(text(f"SELECT id, at FROM {TABLE} ORDER BY id")))
        kinds = {r[0]: r[1] for r in rows}
        assert kinds[3] == "" and kinds[4] is None
        # 这两个就是 D1 迁移的直接障碍：它们不是时间
        assert kinds[5] == "刚刚"


class TestSqliteCastTrap:
    """**踩过的坑**（P1-53）：`batch_alter_table` 在 SQLite 上会静默毁数据。

    Alembic 常见的写法 `batch_alter_table(...).alter_column(...)` 内部用
    `CAST(旧列 AS 新类型)` 拷贝，而 SQLite 把**以数字开头的字符串当数字**：

        SELECT CAST('2026-10-18T06:00:00+00:00' AS DATETIME)  -->  2026

    整列时间被截成年份。**迁移不报错、行数不变、排序看着还正常**
    （两行都变成 2026 之后相对次序恰好没变）——所以"跑一遍看看排序对不对"
    这种验证方式完全发现不了它。必须断言**值本身**。
    """

    def test_sqlite_cast_truncates_iso_string_to_year(self):
        eng = _make_table([(1, "2026-10-18T06:00:00+00:00")])
        casted = eng.connect().execute(
            text(f"SELECT CAST(at AS DATETIME) FROM {TABLE} WHERE id = 1")
        ).scalar()
        assert casted == 2026 or str(casted).startswith("2026") and len(str(casted)) <= 4, (
            f"SQLite 的 CAST 行为变了？当前结果：{casted!r}"
        )

    def test_batch_alter_table_corrupts_values(self):
        """把"用 batch_alter_table 会毁数据"钉成断言。

        这条测试存在的意义是**让下一个人别再用它**：迁移脚本 0030 因此走了
        显式重建 + Python 逐行转换的路径。
        """
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import DateTime

        eng = _make_table([
            (1, "2026-10-18T06:00:00+00:00"),
            (2, "2026-10-17T06:00:00+00:00"),
        ])
        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                from alembic import op

                with op.batch_alter_table(TABLE) as batch:
                    batch.alter_column("at", type_=DateTime(timezone=True), existing_type=String(30))
        values = [r[0] for r in eng.connect().execute(text(f"SELECT at FROM {TABLE} ORDER BY id"))]
        corrupted = any(len(str(v)) < 19 for v in values)
        assert corrupted, (
            f"batch_alter_table 竟然没毁数据（{values}）——要么 SQLite 行为变了，"
            f"要么迁移脚本 0030 的重建路径可以换回简单写法了，两种都值得重新评估"
        )


class TestAlterBehavior:
    """问题 2/3/4：改类型会发生什么。"""

    @pytest.fixture
    def eng_with_iso(self):
        eng = _make_table([(1, "2026-10-18T14:00:00+00:00"), (2, "2026-10-18T09:00:00+00:00")])
        yield eng
        eng.dispose()

    def test_rebuild_path_preserves_values_and_order(self, eng_with_iso):
        """**正确路径**（迁移 0030 用的就是它）：反射 → 建新表 → Python 逐行转换。

        断言三件事：列类型已变、**值完整**（不是被 CAST 截断的年份）、排序仍按时间。
        第三条原版就断言过，但当时没断言值——于是 CAST 毁数据从缝里溜了过去。
        """
        from datetime import datetime, timezone

        import sqlalchemy as sa

        eng = eng_with_iso
        old = sa.Table(TABLE, sa.MetaData(), autoload_with=eng)
        cols = [c.name for c in old.columns]
        new_tbl = sa.Table(
            "probe_new",
            sa.MetaData(),
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("at", sa.DateTime(timezone=True)),
        )
        new_tbl.metadata.create_all(eng)

        with eng.begin() as conn:
            for row in conn.execute(sa.select(old)).fetchall():
                data = {c: getattr(row, c) for c in cols}
                raw = data.get("at")
                dt = datetime.fromisoformat(str(raw)) if raw else None
                conn.execute(new_tbl.insert().values(id=data["id"], at=dt))
            conn.execute(sa.text(f"DROP TABLE {TABLE}"))
            conn.execute(sa.text("ALTER TABLE probe_new RENAME TO " + TABLE))

        cols_after = {c["name"]: c["type"] for c in inspect(eng).get_columns(TABLE)}
        assert "DATETIME" in str(cols_after["at"]).upper(), cols_after

        rows = list(eng.connect().execute(text(f"SELECT id, at FROM {TABLE} ORDER BY at DESC")))
        # 09:00 的早于 14:00，DESC 应先给 14:00
        assert [r[0] for r in rows] == [1, 2]
        # **值必须完整**——这一条是本批新增的，原版只看顺序，给了 CAST 毁数据假信心
        for _id, value in rows:
            assert len(str(value)) >= 19, f"时间被截断：{value!r}"

    def test_empty_string_and_display_text_block_the_migration(self, eng_with_iso):
        """空串与展示文案会让整表重建失败——**这是 D1 动手前必须先清的数据**。

        这里用真实行为断言：要么迁移直接报错，要么转换后这些行变成 NULL。
        两种都不是"想要的结果"，但症状不同，必须提前知道是哪一种。
        """
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from alembic.operations import batch as _batch
        from sqlalchemy import DateTime

        eng = _make_table([
            (1, "2026-10-18T14:00:00+00:00"),
            (2, ""),        # 存量空串
            (3, "刚刚"),     # 误入的展示文案
        ])
        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            try:
                with _batch.batch_alter_table(TABLE, conn) as batch:
                    batch.alter_column("at", type_=DateTime(timezone=True), existing_type=String(30))
                failed = None
            except Exception as exc:  # noqa: BLE001 —— 实验要看的就是它到底怎么炸
                failed = exc
        assert failed is not None, "空串/展示文案竟然没让迁移失败——那更要搞清楚它们去哪了"
        assert isinstance(failed, Exception)

    def test_null_rows_survive_as_null(self, eng_with_iso):
        """NULL 应当原样保留（它是"没有时间"的正确表达，不是脏数据）。"""
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from alembic.operations import batch as _batch
        from sqlalchemy import DateTime

        eng = _make_table([(1, "2026-10-18T14:00:00+00:00"), (2, None)])
        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                from alembic import op

                with op.batch_alter_table(TABLE) as batch:
                    batch.alter_column("at", type_=DateTime(timezone=True), existing_type=String(30))
        rows = dict(eng.connect().execute(text(f"SELECT id, at FROM {TABLE}")).all())
        assert rows[2] is None
        assert rows[1] is not None
