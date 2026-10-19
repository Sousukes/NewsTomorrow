"""P1-74（E4-a）：`scripts/prune_duplicate_news.py` 存量清理脚本。

覆盖：
- **默认只读**：不加 `--apply` 时只报告，一行都不删（这是脚本的安全边界，
  必须有测试守着，否则将来有人"顺手"改掉默认值不会被发现）；
- `--apply` 后按组保留一条、删除其余，且保留的是**发布时间最新**的那条；
- **有依赖行 / 有互动计数**的行不删，只报告（删了就是丢数据）；
- 隐藏行不参与分组（与 `find_duplicate` 同口径）；
- 跨源同标题不分组（E4 跨源部分已证伪）；
- 互动量高的一方被保留（哪怕它发布时间更旧）；
- 没有重复时不写库、返回 0。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Comment, News  # noqa: E402
from scripts import prune_duplicate_news as prune  # noqa: E402

TITLE = "宁夏枸杞鲜果首次经银川关区冷链直供香港"


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def run(monkeypatch):
    """以给定 argv 跑一遍脚本，返回 (退出码, 输出)。"""

    def _run(*argv: str):
        monkeypatch.setattr("sys.argv", ["prune_duplicate_news.py", *argv])
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = prune.main()
        return code, buf.getvalue()

    return _run


def _news(nid: str, title: str = TITLE, source_key="src_a", published_at=None, **kw):
    row = News(
        id=nid,
        title=title,
        summary="摘要",
        source=f"源-{source_key}",
        source_key=source_key,
        comment_count=0,
        time="1小时前",
        channel="finance",
        variant="text",
        published_at=published_at,
        hidden=False,
    )
    for k, v in kw.items():
        setattr(row, k, v)
    with SessionLocal() as db:
        db.add(row)
        db.commit()
    return row


def _ids() -> list[str]:
    with SessionLocal() as db:
        return [r[0] for r in db.execute(select(News.id)).all()]


class TestReadOnlyByDefault:
    def test_no_apply_leaves_db_untouched(self, run):
        _news("n1", published_at="2026-10-10T12:56:44")
        _news("n2", published_at="2026-10-10T13:39:30")

        code, out = run()

        assert code == 0
        assert "只读模式" in out  # 明确告知使用者没有写库
        assert set(_ids()) == {"n1", "n2"}

    def test_apply_deletes_and_keeps_latest(self, run):
        _news("n1", published_at="2026-10-10T12:56:44")
        _news("n2", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert code == 0
        assert "已删除 1 条" in out
        assert _ids() == ["n2"]  # 保留发布时间最新的那条

    def test_no_duplicates_is_noop(self, run):
        _news("n1", published_at="2026-10-10T12:56:44")

        code, out = run("--apply")

        assert code == 0
        assert "没有发现同源重复" in out
        assert _ids() == ["n1"]


class TestSkipRules:
    def test_row_with_comment_is_skipped(self, run):
        """有待删行的评论：不删，只报告——删了评论就成孤儿了。"""
        _news("n1", published_at="2026-10-10T12:56:44")
        _news("n2", published_at="2026-10-10T13:39:30")
        with SessionLocal() as db:
            db.add(Comment(id="c1", news_id="n1", user_name="甲", content="评论"))
            db.commit()

        code, out = run("--apply")

        assert code == 0
        assert "跳过" in out and "评论×1" in out
        assert set(_ids()) == {"n1", "n2"}  # 一条都没删

    def test_row_with_interaction_count_is_skipped(self, run):
        """两条互动量**相同**：保留新的那条，旧的那条计数不能随删除一起丢。

        互动量是选主键的第一顺位，所以单条有计数的行一定会成为主键；这条
        分支真正防的是"并列时删掉另一条，它的计数凭空消失"。
        """
        _news("n1", published_at="2026-10-10T12:56:44", comment_count=3)
        _news("n2", published_at="2026-10-10T13:39:30", comment_count=3)

        code, out = run("--apply")

        assert code == 0
        assert "跳过" in out and "互动计数" in out
        assert set(_ids()) == {"n1", "n2"}  # 一条都没删

    def test_higher_mass_row_is_kept_even_if_older(self, run):
        """互动量优先于发布时间：有数据的那条才是要保留的。"""
        _news("n1", published_at="2026-10-10T12:56:44", comment_count=5, play_count=10)
        _news("n2", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert code == 0
        assert _ids() == ["n1"]


class TestGrouping:
    def test_cross_source_not_grouped(self, run):
        """跨源同标题不分组（E4 跨源部分已证伪：不存在安全阈值）。"""
        _news("n1", source_key="src_a", published_at="2026-10-10T12:56:44")
        _news("n2", source_key="src_b", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert "没有发现同源重复" in out
        assert set(_ids()) == {"n1", "n2"}

    def test_hidden_rows_not_grouped(self, run):
        """隐藏行不参与分组（与 `find_duplicate` 同口径）。"""
        _news("n1", published_at="2026-10-10T12:56:44", hidden=True)
        _news("n2", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert "没有发现同源重复" in out
        assert set(_ids()) == {"n1", "n2"}

    def test_normalized_title_variants_grouped(self, run):
        """标点/全角差异算同一组（归一化标题相等）。"""
        _news("n1", title=TITLE, published_at="2026-10-10T12:56:44")
        _news("n2", title=TITLE + "！！", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert code == 0
        assert _ids() == ["n2"]

    def test_empty_normalized_title_not_grouped(self, run):
        """标题归一后为空的条目不参与判重（判不了，也不该判）。"""
        _news("n1", title="！！！", published_at="2026-10-10T12:56:44")
        _news("n2", title="？？？", published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert "没有发现同源重复" in out
        assert set(_ids()) == {"n1", "n2"}

    def test_null_source_key_groups_together(self, run):
        """`source_key` 为 NULL 的历史行归为同一组。"""
        _news("n1", source_key=None, published_at="2026-10-10T12:56:44")
        _news("n2", source_key=None, published_at="2026-10-10T13:39:30")

        code, out = run("--apply")

        assert code == 0
        assert _ids() == ["n2"]
