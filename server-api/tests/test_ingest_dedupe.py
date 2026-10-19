"""P1-74（E4-a）：同源重复入库（`upsert_news` 的同源近邻判重）。

覆盖：
- `normalize_title` 的归一语义（NFKC / 大小写 / 标点 / 空白 / 符号 / 控制字符）；
- **真近邻合并**：同源、id 不同、标题只有琐碎差异 → 命中判重，不新增行，
  且**保留旧行**（id 不变、互动数据不丢、易变字段被增量刷新）；
- **反例不合并**：CBA 季前赛两场不同的比赛（实测相似度 0.652，见设计文档 §三）
  → 两条都在；
- **SQLite 退化**：无 pg_trgm 时只认归一化相等（同标题仍能判掉、改写判不掉）；
- **批量内自判重**：同一批里两条互为近邻，第二条不能新增；
- **跨源不合并**（E4 已证伪的那一半）：同标题不同 source_key 各自成行；
- 按 id 命中的再抓**不算** deduped（那是更新，不是挡下重复）；
- 隐藏行不参与判重（已下线的稿子不该把新稿吸走）；
- `trgm.available()` 在 SQLite 上的退化。

关于 `similarity()`：PG 路径依赖 `func.similarity`，而测试跑在 SQLite 上。
这里沿用 P1-70 的做法——在 SQLite 连接上注册一个同名三元组函数（与 pg_trgm
同公式），让**判重路径真的被执行**。绝对值的正确性由
`scripts/verify_search_ranking.py` 在真实 PG 上核对；本文件断言的是
"合并 / 不合并"这个**离散**结果，而它只取决于阈值 0.9 与两组实测值
（0.652 / 1.000）之间的间隔——所以两个方向都带**防空转断言**：
先证明待测标题对确实落在阈值的哪一侧，再断言入库结果。
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_SAMPLE_DATA"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FEED_CRON_ENABLED"] = "false"

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app import trgm as trgm_mod  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.feeds import ingest as ingest_mod  # noqa: E402
from app.feeds.fetcher import RawItem  # noqa: E402
from app.models import News  # noqa: E402
from app.routers.search import trigram_similarity  # noqa: E402

# ---- 真实数据里量到的两档（docs/后端/E4多源转载去重设计.md §三）----
# 同源、同标题（源换了链接重发同一篇）——要合并的那一档
SAME_TITLE = "国家发改委就促进民间投资高质量发展若干措施答记者问"
# CBA 季前赛两场不同的比赛——不能合并的那一档（相似度 0.652）
CBA_TIANJIN = "CBA季前赛汕头站：广州队不敌天津队"
CBA_SHANDONG = "CBA季前赛汕头站：广州队不敌山东队"

_SIMILARITY_INSTALLED = False


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    # trgm 的探测结果是进程级缓存：换库/换方言后必须清掉，否则本文件之后
    # 的文件会拿到上一轮的结论（表现为"断言随机失败，且只在全量跑时出现"）
    trgm_mod.reset()


def _install_sqlite_similarity(eng) -> None:
    """在 SQLite 上注册 `similarity(a, b)`（幂等）。

    两个入口都要照顾到：`event` 监听管**未来**新建的连接，而对**已存在**的
    连接（StaticPool 会一直复用同一条）必须直接补装——否则本文件的首个用例
    拿不到这个函数，报的是"no such function"，看起来像代码缺陷。
    """

    def _apply(dbapi_conn):
        # 参数为 NULL 时 SQLite 传 None；pg_trgm 对空串同样返回 0
        dbapi_conn.create_function(
            "similarity", 2, lambda a, b: trigram_similarity(a or "", b or "")
        )

    global _SIMILARITY_INSTALLED
    if not _SIMILARITY_INSTALLED:
        event.listen(eng, "connect", lambda dbapi_conn, _rec: _apply(dbapi_conn))
        _SIMILARITY_INSTALLED = True
    with eng.connect() as conn:
        _apply(conn.connection.dbapi_connection)


@pytest.fixture
def trgm_on(monkeypatch):
    """让 SQLite 也走 pg_trgm 路径：注册同名函数 + 声明扩展可用。"""
    _install_sqlite_similarity(engine)
    monkeypatch.setattr(trgm_mod, "available", lambda db: True)


def _item(nid: str, title: str, source_key: str = "src_a", **kw) -> RawItem:
    base = dict(
        id=nid,
        title=title,
        source=f"源-{source_key}",
        source_key=source_key,
        summary="摘要",
        channel="finance",
        published_at=datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return RawItem(**base)


def _upsert(items: list[RawItem]):
    with SessionLocal() as db:
        return ingest_mod.upsert_news(db, items)


def _rows() -> list[News]:
    with SessionLocal() as db:
        return db.query(News).order_by(News.id).all()


def _long_unique_title(swap_to: str | None = None, swap_at: int = 30) -> str:
    """64 个互不相同汉字的标题。

    为什么必须这么长：三元组相似度对**短标题**几乎没有分辨力——8 字标题改两个
    字只有 0.65（CBA 反例），改一个字更低，永远够不到 0.9 的门槛。长度到 64
    时改一个字是 62/68 ≈ 0.912，才落进"确实是同一篇"那一档。
    字符两两不同是为了让三元组集合不塌缩（有重复字符时集合会远小于长度，
    相似度随之虚高/虚低，测试就不再说明它想说明的事）。
    """
    chars = [chr(0x4E00 + i) for i in range(64)]
    if swap_to is not None:
        chars[swap_at] = swap_to
    return "".join(chars)


# ----------------------------- 归一化 -----------------------------


class TestNormalizeTitle:
    def test_fullwidth_and_case_fold(self):
        assert ingest_mod.normalize_title("ＡＩ大会ＡＩ") == "ai大会ai"

    def test_strips_punct_space_and_symbols(self):
        # 全角空格（Zs）、感叹号（Po）、括号（Ps/Pe）全部去掉
        assert ingest_mod.normalize_title("标题！！　（附）") == "标题附"

    def test_strips_control_and_format_chars(self):
        # 零宽空格（Cf）与软连字符（Cf）都是"看不见但算一个字符"的差异
        assert ingest_mod.normalize_title("标\u200b题\u00ad") == "标题"

    def test_empty_and_punct_only(self):
        assert ingest_mod.normalize_title("") == ""
        assert ingest_mod.normalize_title("！！！") == ""

    def test_nfkc_compatibility(self):
        # 兼容字符（如全角数字、带圈数字）先 NFKC 再判等
        assert ingest_mod.normalize_title("第①②届") == "第12届"


# ----------------------------- 真近邻合并 -----------------------------


class TestNearDuplicateMerge:
    def test_same_title_different_id_merges(self):
        """根因场景：源改了链接，`_stable_id` 算不出同一个 id，按 id 判重失效。"""
        _upsert([_item("n1", SAME_TITLE, published_at=datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc))])
        rep = _upsert(
            [_item("n2", SAME_TITLE, published_at=datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc))]
        )

        assert (rep.inserted, rep.updated, rep.deduped) == (0, 1, 1)
        rows = _rows()
        assert [r.id for r in rows] == ["n1"]  # 保留旧行，不新增

    def test_punctuation_variant_merges_without_trgm(self):
        """SQLite 退化路径也要吃掉最常见的那类琐碎差异。"""
        _upsert([_item("n1", "某发布会定档十月")])
        rep = _upsert([_item("n2", "某发布会定档十月！！")])

        assert (rep.inserted, rep.deduped) == (0, 1)
        assert len(_rows()) == 1

    def test_fullwidth_variant_merges_without_trgm(self):
        _upsert([_item("n1", "AI大会发布新品")])
        rep = _upsert([_item("n2", "ＡＩ大会发布新品")])

        assert (rep.inserted, rep.deduped) == (0, 1)
        assert len(_rows()) == 1

    def test_rewrite_not_merged_without_trgm(self):
        """SQLite 退化：只认归一化相等，源改标题重发判不掉（刻意，见设计 §三）。"""
        _upsert([_item("n1", CBA_TIANJIN)])
        rep = _upsert([_item("n2", CBA_SHANDONG)])

        assert (rep.inserted, rep.updated, rep.deduped) == (1, 0, 0)
        assert len(_rows()) == 2

    def test_long_title_one_char_diff_merges_with_trgm(self, trgm_on):
        """pg_trgm 路径：长标题改一个字（相似度 ≈ 0.912）算同一篇。"""
        a = _long_unique_title()
        b = _long_unique_title(swap_to="\u9fa5")
        # 防空转：先证明这一对落在阈值的"合并"一侧，且归一化**不**相等
        # （否则这条断言会被更便宜的第一档判据满足，trgm 分支仍未被验证）
        assert ingest_mod.normalize_title(a) != ingest_mod.normalize_title(b)
        assert trigram_similarity(a, b) >= ingest_mod.DUP_SIMILARITY

        _upsert([_item("n1", a)])
        rep = _upsert([_item("n2", b)])

        assert (rep.inserted, rep.updated, rep.deduped) == (0, 1, 1)
        assert [r.id for r in _rows()] == ["n1"]

    def test_cba_counter_example_not_merged_with_trgm(self, trgm_on):
        """真实库里的反例：同主题不同稿件，相似度 0.652，**不能**合并。"""
        # 防空转：先证明这一对落在阈值的"不合并"一侧
        assert trigram_similarity(CBA_TIANJIN, CBA_SHANDONG) < ingest_mod.DUP_SIMILARITY
        assert ingest_mod.normalize_title(CBA_TIANJIN) != ingest_mod.normalize_title(
            CBA_SHANDONG
        )

        _upsert([_item("n1", CBA_TIANJIN)])
        rep = _upsert([_item("n2", CBA_SHANDONG)])

        assert (rep.inserted, rep.updated, rep.deduped) == (1, 0, 0)
        assert len(_rows()) == 2

    def test_measured_similarity_matches_pg(self):
        """垫片与 pg_trgm 的口径对齐：CBA 反例的相似度就是设计文档记的 0.652。

        这条不是形式主义——如果 `trigram_similarity` 与 pg_trgm 的公式漂了，
        上面两条"合并/不合并"的结论会整体偏移，而它们自己看不出这件事。
        """
        assert trigram_similarity(CBA_TIANJIN, CBA_SHANDONG) == pytest.approx(0.652, abs=0.01)


# ----------------------------- 批量内自判重 -----------------------------


class TestBatchSelfDedupe:
    def test_two_in_one_batch(self):
        """同一批里两条互为近邻：第二条还没落库，查库查不到，必须靠批内记账。"""
        rep = _upsert([_item("n1", SAME_TITLE), _item("n2", SAME_TITLE)])

        assert (rep.inserted, rep.updated, rep.deduped) == (1, 1, 1)
        assert [r.id for r in _rows()] == ["n1"]

    def test_cross_source_not_merged(self):
        """跨源**刻意**不合并：E4 的跨源部分已被证伪（两个分布重叠，无安全阈值）。"""
        rep = _upsert([_item("n1", SAME_TITLE, source_key="src_a"),
                       _item("n2", SAME_TITLE, source_key="src_b")])

        assert (rep.inserted, rep.updated, rep.deduped) == (2, 0, 0)
        assert len(_rows()) == 2

    def test_null_source_key_groups_together(self):
        """`source_key` 为空的条目归为同一组（库里是 NULL，不是空串）。"""
        rep = _upsert([_item("n1", SAME_TITLE, source_key=""),
                       _item("n2", SAME_TITLE, source_key="")])

        assert (rep.inserted, rep.deduped) == (1, 1)
        assert [r.source_key for r in _rows()] == [None]

    def test_id_hit_is_update_not_dedupe(self):
        """按 id 命中是"同一条被再抓到"，走更新路径，不计入挡下的重复。"""
        _upsert([_item("n1", SAME_TITLE)])
        rep = _upsert([_item("n1", SAME_TITLE)])

        assert (rep.inserted, rep.updated, rep.deduped) == (0, 1, 0)

    def test_empty_title_not_deduped(self):
        """标题归一后为空的条目不参与判重（判不了，也不该判）。"""
        rep = _upsert([_item("n1", ""), _item("n2", "")])

        assert (rep.inserted, rep.updated, rep.deduped) == (2, 0, 0)


# ----------------------------- 命中后的语义 -----------------------------


class TestDedupeHitSemantics:
    def test_keeps_existing_row_and_interactions(self):
        """命中判重时保留旧行的 id 与已积累的互动数据，只增量刷新易变字段。"""
        _upsert([_item("n1", SAME_TITLE)])
        with SessionLocal() as db:
            row = db.get(News, "n1")
            row.comment_count = 7
            row.play_count = 123
            db.commit()

        rep = _upsert([_item("n2", SAME_TITLE, summary="新摘要", content="新正文")])

        assert rep.deduped == 1
        row = _rows()[0]
        assert row.id == "n1"
        assert row.comment_count == 7  # 互动数据不被抹掉
        assert row.play_count == 123
        assert row.summary == "新摘要"  # 易变字段被刷新
        assert row.content == "新正文"

    def test_source_rename_still_dedupes_by_key(self):
        """E6b：判重认 `source_key` 而非名称快照——运营改名后同源仍能判出来。"""
        _upsert([_item("n1", SAME_TITLE, source_key="src_a")])
        rep = _upsert([_item("n2", SAME_TITLE, source_key="src_a", source="源-新名")])

        assert rep.deduped == 1
        row = _rows()[0]
        assert row.id == "n1"
        assert row.source == "源-新名"  # 名称快照跟着刷新

    def test_hidden_rows_not_considered(self):
        """已下线的稿子不参与判重（否则新稿会被隐藏稿"吸走"，直接消失）。"""
        _upsert([_item("n1", SAME_TITLE)])
        with SessionLocal() as db:
            db.get(News, "n1").hidden = True
            db.commit()

        rep = _upsert([_item("n2", SAME_TITLE)])

        assert (rep.inserted, rep.deduped) == (1, 0)
        assert len(_rows()) == 2

    def test_find_duplicate_returns_none_without_match(self):
        with SessionLocal() as db:
            assert ingest_mod.find_duplicate(db, "src_a", "完全另一篇稿件") is None

    def test_find_duplicate_matches_by_normalized_title(self):
        _upsert([_item("n1", SAME_TITLE, source_key="src_a")])
        with SessionLocal() as db:
            row = ingest_mod.find_duplicate(db, "src_a", SAME_TITLE + "！")
            assert row is not None and row.id == "n1"
            # 换一个源就找不到
            assert ingest_mod.find_duplicate(db, "src_b", SAME_TITLE) is None


# ----------------------------- trgm 探测 -----------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeDb:
    """只实现 `available()` 用到的那几个属性的假会话（PG 分支不需要真库）。"""

    def __init__(self, dialect: str, value=None, exc: Exception | None = None):
        self.bind = type("Bind", (), {"dialect": type("D", (), {"name": dialect})()})()
        self._value = value
        self._exc = exc
        self.executed = False

    def execute(self, *_a, **_kw):
        self.executed = True
        if self._exc is not None:
            raise self._exc
        return _FakeResult(self._value)


class TestTrgmProbe:
    def test_unavailable_on_sqlite(self):
        with SessionLocal() as db:
            assert trgm_mod.available(db) is False

    def test_reset_clears_cache(self):
        with SessionLocal() as db:
            assert trgm_mod.available(db) is False
            trgm_mod.reset()
            assert trgm_mod._CHECKED is False  # noqa: SLF001 - 断言的是缓存本身

    def test_pg_with_extension(self):
        trgm_mod.reset()
        assert trgm_mod.available(_FakeDb("postgresql", value=1)) is True

    def test_pg_without_extension_degrades(self):
        """扩展没建（迁移 0005 因权限跳过）时必须退回精确口径，而不是报错。"""
        trgm_mod.reset()
        assert trgm_mod.available(_FakeDb("postgresql", value=None)) is False

    def test_probe_failure_degrades(self):
        """探测本身炸了（连不上/无权限查 pg_extension）同样退回精确口径。"""
        trgm_mod.reset()
        db = _FakeDb("postgresql", exc=RuntimeError("permission denied"))
        assert trgm_mod.available(db) is False

    def test_other_dialect_never_probed(self):
        """非 PG 方言直接判不可用，不发查询（MySQL 上没有 pg_extension 可查）。"""
        trgm_mod.reset()
        db = _FakeDb("mysql")
        assert trgm_mod.available(db) is False
        assert db.executed is False
