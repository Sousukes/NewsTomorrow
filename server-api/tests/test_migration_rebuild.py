"""`app.migration_utils.convert_column_type` 的通用场景测试。

P1-53 里那三次「搬漏了」（索引、外键、`ondelete`）都发生在**单表单列**的
一次性迁移里。这个文件把 helper 放到多表多列、多索引、多外键、NULL、脏值
这些场景下测一遍——**迁移脚本不再各自实现一遍重建逻辑**，于是也就没有
"下一个迁移又忘了搬索引"的机会。

运行：
    cd server-api
    pytest tests/test_migration_rebuild.py -q
"""
import os

import pytest
import sqlalchemy as sa

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.migration_utils import convert_column_type, parse_utc  # noqa: E402


@pytest.fixture
def eng():
    e = sa.create_engine("sqlite:///:memory:")
    md = sa.MetaData()
    sa.Table(
        "parent",
        md,
        sa.Column("key", sa.String(20), primary_key=True),
    )
    sa.Table(
        "child",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_key", sa.String(20), sa.ForeignKey("parent.key", ondelete="CASCADE")),
        sa.Column("at", sa.String(30)),
        sa.Column("note", sa.String(50)),
        sa.Index("ix_child_parent_at", "parent_key", "at"),
    )
    md.create_all(e)
    with e.begin() as conn:
        conn.execute(sa.text("INSERT INTO parent (key) VALUES ('p1'),('p2')"))
        conn.execute(
            sa.text(
                "INSERT INTO child (id, parent_key, at, note) VALUES "
                "(1,'p1','2026-10-18T06:00:00+00:00','a'),"
                "(2,'p1','2026-10-17T06:00:00+00:00','b'),"
                "(3,'p2',NULL,NULL),"
                "(4,'p2','刚刚',NULL)"      # 脏值：不可解析 → 写 NULL 且计数
            )
        )
    yield e
    e.dispose()


def _convert(e, table="child", column="at"):
    """迁移里 helper 拿到的是 **Connection**（`op.get_bind()`），不是 Engine——
    SQLAlchemy 2.x 已移除 `Engine.execute`。测试照真实的调用方式来。"""
    with e.begin() as conn:
        return convert_column_type(conn, table, column, sa.DateTime(timezone=True))


def _child_rows(e):
    return e.connect().execute(sa.text("SELECT id, at, note FROM child ORDER BY id")).fetchall()


class TestConvertColumnType:
    def test_values_are_preserved_and_not_truncated(self, eng):
        """**值本身**必须完整——P1-53 的 CAST 陷阱毁掉的就是这里，
        而当时"排序看着正常"给了假信心。这里断言长度与可解析性。"""
        res = _convert(eng)
        assert res["rows"] == 4
        rows = _child_rows(eng)
        by_id = {r[0]: r for r in rows}
        for cid in (1, 2):
            assert len(str(by_id[cid][1])) >= 19, f"时间被截断：{by_id[cid][1]}"
            from datetime import datetime

            datetime.fromisoformat(str(by_id[cid][1]))

    def test_null_and_unparseable_become_null_and_are_counted(self, eng):
        res = _convert(eng)
        by_id = {r[0]: r for r in _child_rows(eng)}
        assert by_id[3][1] is None, "NULL 不该被编造成时间"
        assert by_id[4][1] is None, "不可解析的值应写 NULL 而不是留垃圾"
        assert res["skipped"] == 1, res
        assert res["converted"] == 2, res

    def test_other_columns_untouched(self, eng):
        _convert(eng)
        by_id = {r[0]: r for r in _child_rows(eng)}
        assert by_id[1][2] == "a" and by_id[2][2] == "b"
        assert by_id[3][2] is None

    def test_index_is_recreated_with_its_original_name_and_columns(self, eng):
        """复合索引必须**按反射到的真名与真列**回来。

        自造名字相近的索引会把「复合索引没了」掩盖成「名字不对」——
        P1-53 踩过一次。
        """
        _convert(eng)
        idx = sa.inspect(eng).get_indexes("child")
        names = [i["name"] for i in idx]
        assert "ix_child_parent_at" in names, names
        cols = next(i for i in idx if i["name"] == "ix_child_parent_at")["column_names"]
        assert list(cols) == ["parent_key", "at"], cols

    def test_foreign_key_keeps_ondelete_cascade(self, eng):
        """`ON DELETE CASCADE` 不能丢——丢了源删时子行不清理，且毫无症状。"""
        _convert(eng)
        fks = sa.inspect(eng).get_foreign_keys("child")
        assert fks, "外键丢了"
        assert fks[0]["referred_table"] == "parent"
        assert (fks[0]["options"] or {}).get("ondelete") == "CASCADE", fks[0]

    def test_cascade_actually_still_works(self, eng):
        """不只是元数据对，**行为也要对**：删父行，子行应当被级联清掉。"""
        _convert(eng)
        with eng.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            conn.execute(sa.text("DELETE FROM parent WHERE key='p1'"))
        left = [r[0] for r in _child_rows(eng)]
        assert 1 not in left and 2 not in left, f"CASCADE 没生效，残留：{left}"
        assert 3 in left and 4 in left, "p2 的子行不该被删"

    def test_tmp_table_is_gone(self, eng):
        _convert(eng)
        tables = sa.inspect(eng).get_table_names()
        assert not [t for t in tables if "conv_tmp" in t], tables

    def test_column_type_actually_changed(self, eng):
        _convert(eng)
        cols = {c["name"]: c["type"] for c in sa.inspect(eng).get_columns("child")}
        assert "DATETIME" in str(cols["at"]).upper(), cols
        assert "VARCHAR" in str(cols["note"]).upper(), cols


class TestNotNullColumnWithDirtyValue:
    """NOT NULL 列上混入不可解析的值：**必须明确失败**，不得静默处理。

    两种"静默"都很坏：写 NULL 会撞约束报 `IntegrityError`（错误信息里只有
    "NOT NULL 约束"，看不出根因是"值不是时间"）；编造一个时间则是**造数据**。
    所以 helper 收集脏行、删掉临时表、抛带样本的 RuntimeError，并保证旧表未动。
    """

    def test_aborts_with_sample_and_leaves_old_table_intact(self, eng):
        with eng.begin() as conn:
            conn.execute(sa.text("DELETE FROM child WHERE id = 4"))   # 先去掉脏行
            conn.execute(sa.text("ALTER TABLE child RENAME TO child_clean"))
            # 这个用例只验"脏值 → 明确失败"，不需要外键
            #（外键保留由 TestConvertColumnType 覆盖）。顺带一提：不带外键也省得
            # 重新踩一遍"新表要挂在同一 MetaData"的坑。
            sa.Table(
                "child",
                sa.MetaData(),
                sa.Column("id", sa.Integer, primary_key=True),
                sa.Column("parent_key", sa.String(20)),
                sa.Column("at", sa.String(30), nullable=False),   # NOT NULL
                sa.Column("note", sa.String(50)),
            ).create(conn)
            conn.execute(sa.text("INSERT INTO child (id, parent_key, at, note) VALUES (9,'p1','刚刚',NULL)"))

        with eng.begin() as conn:
            with pytest.raises(RuntimeError) as exc:
                convert_column_type(conn, "child", "at", sa.DateTime(timezone=True))
        assert "无法解析" in str(exc.value)
        assert "刚刚" in str(exc.value), "错误信息里要能看到脏值样本"

        # 旧表必须完好（不能迁移到一半）
        names = sa.inspect(eng).get_table_names()
        assert "child" in names
        assert not [n for n in names if "conv_tmp" in n], names
        left = eng.connect().execute(sa.text("SELECT at FROM child")).all()
        assert left == [("刚刚",)], left


class TestParseUtc:
    def test_naive_is_read_as_utc(self):
        """与 0027 回填、`models.parse_dt` 同一口径：naive 按 UTC 解释。"""
        from datetime import datetime, timezone

        dt = parse_utc("2026-10-18T06:00:00")
        assert dt.tzinfo is not None
        assert dt.astimezone(timezone.utc).hour == 6

    def test_aware_is_converted_not_kept(self):
        from datetime import datetime

        dt = parse_utc("2026-10-18T14:00:00+08:00")
        assert dt.isoformat().startswith("2026-10-18T06:00:00")

    def test_garbage_and_empty_are_none(self):
        assert parse_utc("刚刚") is None
        assert parse_utc("") is None
        assert parse_utc(None) is None

    def test_datetime_passthrough_keeps_instant(self):
        from datetime import datetime, timezone

        aware = datetime(2026, 10, 18, 6, 0, tzinfo=timezone.utc)
        assert parse_utc(aware) == aware
        naive = datetime(2026, 10, 18, 6, 0)
        assert parse_utc(naive).tzinfo is not None